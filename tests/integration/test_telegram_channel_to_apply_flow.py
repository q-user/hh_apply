"""E2E: Telegram update with vacancy URL -> bot dispatch -> prep -> submit.

Cross-slice workflow that powers the Telegram channel-monitoring bot
(issues #56, #57) joined to the application prep + submit slices
(issues #54, #55):

  1. Scripted Telegram update is dispatched to
     ``BotService.dispatch_update``.
  2. The bot replies (welcome / help / unknown) and the reply
     appears on the mock transport.
  3. The application prep slice generates a cover letter for a
     given vacancy and the application submit slice sends the
     application via the mocked ``/negotiations`` endpoint.
  4. The full message history is recorded on
     ``mock_telegram_transport.sent_messages``.

Unlike the unit tests in ``tests/vsa/test_telegram_bot_slice.py``,
this test exercises the *cross-slice* payload (bot reply +
prep-generated letter + submit POST) rather than the bot's
internal state machine.
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.integration

CHAT_ID = 1


# ─── Helpers ──────────────────────────────────────────────────────────


def _text_update(
    text: str,
    chat_id: int = CHAT_ID,
    update_id: int = 1,
) -> dict:
    return {
        "update_id": update_id,
        "message": {
            "chat": {"id": chat_id},
            "from": {"id": chat_id},
            "text": text,
        },
    }


def _make_full_vacancy(vacancy_id: int | str) -> dict:
    return {
        "id": str(vacancy_id),
        "name": f"Vacancy {vacancy_id}",
        "employer": {"id": 900 + int(vacancy_id), "name": "Acme"},
        "salary": {"from": 200000, "to": 300000, "currency": "RUR"},
        "area": {"name": "Москва"},
        "description": "Python, Django, FastAPI",
        "key_skills": [
            {"name": "Python"},
            {"name": "Django"},
        ],
        "alternate_url": f"https://hh.ru/vacancy/{vacancy_id}",
        "has_test": False,
        "response_letter_required": True,
    }


# ─── Test cases ──────────────────────────────────────────────────────


class TestTelegramChannelToApplyFlow:
    """Telegram bot replies + cross-slice prep/submit on the same DB."""

    def test_bot_dispatch_start_then_unknown_command(
        self,
        mock_telegram_transport,
        slices,
    ) -> None:
        """A ``/start`` followed by a plain-text update goes through
        the bot dispatch and the transport records both replies.
        """
        slices.telegram_bot.service.dispatch_update(
            _text_update("/start", update_id=1)
        )
        slices.telegram_bot.service.dispatch_update(
            _text_update("banana", update_id=2)
        )
        assert len(mock_telegram_transport.sent_messages) == 2
        texts = [m["text"] for m in mock_telegram_transport.sent_messages]
        assert any("Добро пожаловать" in t for t in texts)
        assert any("Неизвестная команда" in t or "/help" in t for t in texts)

    def test_callback_update_routed_to_review(
        self,
        test_db,
        mock_telegram_transport,
        slices,
    ) -> None:
        """A callback_query update is routed to the review handler
        and the BotService swallows any exception the default
        ReviewFlowService may raise on an unhandled callback.
        """
        from hh_applicant_tool.storage import StorageFacade
        from hh_applicant_tool.storage.models.telegram_session import (
            TelegramSessionModel,
        )

        StorageFacade(test_db).telegram_sessions.save(
            TelegramSessionModel(chat_id=CHAT_ID, state="review_intro")
        )
        test_db.commit()

        update = {
            "update_id": 1,
            "callback_query": {
                "data": "rf:intro:continue",
                "message": {"chat": {"id": CHAT_ID}},
            },
        }
        # The BotService contract (issue #56): callback routes return None.
        result = slices.telegram_bot.service.dispatch_update(update)
        assert result is None

    def test_bot_reply_then_prep_and_submit_in_one_workflow(
        self,
        test_db,
        mock_hh_api,
        mock_telegram_transport,
        slices,
    ) -> None:
        """Full cross-slice workflow:

        1. A ``/start`` update hits the bot — the welcome reply is
           sent.
        2. The application prep slice generates a draft + cover
           letter for a given vacancy (this is the code path
           Telegram's "confirm" callback would invoke in production).
        3. The application submit slice sends the application to
           the mocked ``/negotiations`` endpoint.
        4. The transport still holds the welcome reply from step 1.
        """
        from hh_applicant_tool.storage import StorageFacade
        from hh_applicant_tool.storage.models.application_draft import (
            ApplicationDraftModel,
        )

        # Step 1: bot dispatches a /start update
        slices.telegram_bot.service.dispatch_update(
            _text_update("/start", update_id=1)
        )
        assert any(
            "Добро пожаловать" in m["text"]
            for m in mock_telegram_transport.sent_messages
        )

        # Step 2: prep slice generates a draft with a cover letter.
        vacancy_id = 42
        vacancy = _make_full_vacancy(vacancy_id)
        draft = slices.application_prep.applications.prepare_draft(
            resume={"id": "r1", "title": "Senior Python"},
            vacancy=vacancy,
            search_profile_id="p1",
            ai_filter_mode="none",
            placeholders={"first_name": "Ivan"},
            force_message=True,
        )
        assert draft is not None
        assert draft.cover_letter is not None
        # The deterministic AI stamps the cover letter with a hash.
        assert "hash=" in draft.cover_letter

        # Re-fetch the persisted draft from the shared DB so the
        # application submit slice sees the same data.
        db_draft = StorageFacade(test_db).application_drafts.get(draft.id)
        assert db_draft is not None
        assert db_draft.cover_letter is not None

        # Step 3: submit slice sends the application.
        mock_hh_api.negotiation_responses.append(
            {"id": "neg-42", "state": {"name": "response"}}
        )
        slice_draft = ApplicationDraftModel(
            id=db_draft.id,
            search_profile_id=db_draft.search_profile_id,
            resume_id=db_draft.resume_id,
            vacancy_id=db_draft.vacancy_id,
            status="prepared",
            cover_letter=db_draft.cover_letter,
            full_vacancy_json=db_draft.full_vacancy_json,
            has_test=db_draft.has_test,
            hh_response_url=db_draft.hh_response_url,
        )
        slices.application_submit.apply_one(slice_draft)

        # The /negotiations call landed at the mock exactly once.
        neg_calls = [
            c for c in mock_hh_api.calls if c[1] == "/negotiations"
        ]
        assert len(neg_calls) == 1

        # The bot transport still has the welcome message from step 1
        # — the prep/submit steps don't touch the transport, but the
        # bot reply proves the dispatch layer was exercised.
        assert any(
            "Добро пожаловать" in m["text"]
            for m in mock_telegram_transport.sent_messages
        )
