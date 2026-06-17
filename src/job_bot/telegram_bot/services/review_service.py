"""Интерактивный пошаговый ревью черновиков откликов в Telegram (issue #9, VSA — issue #87).

VSA-слайс ``job_bot.telegram_bot`` теперь владеет FSM ревью; legacy
модуль :mod:`hh_applicant_tool.services.review_flow` остаётся тонким
shim-ом для обратной совместимости (DeprecationWarning).

Состояния персистятся в ``telegram_sessions.state`` (issue #1). После
перезапуска :meth:`ReviewFlowService.resume_session` восстанавливает сессию
по ``chat_id`` и продолжает ровно с того шага, на котором пользователь
остановился.

Зависимости (DI): ``storage``, ``transport``, ``config``, ``clock``
(по умолчанию ``SystemClock``), ``ai_client`` (опциональный
:class:`AIClientPort`).
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any, cast

from job_bot._legacy_compat.storage.facade import StorageFacade
from job_bot._legacy_compat.storage.models.application_draft import (
    ApplicationDraftModel,
)
from job_bot._legacy_compat.storage.models.application_test_answer import (
    ApplicationTestAnswerModel,
)
from job_bot._legacy_compat.storage.models.apply_job import ApplyJobModel
from job_bot._legacy_compat.storage.models.telegram_session import (
    TelegramSessionModel,
)
from job_bot.telegram_bot.telegram_transport import TelegramTransport
from job_bot.telegram_bot.models.message import InlineButton, OutgoingMessage

if TYPE_CHECKING:
    from job_bot.shared.ports import AIClientPort, Clock

logger = logging.getLogger(__package__)
# Callback_data (формат: rf:<state>:<action>) и целевые объекты регенерации.
CB_INTRO_CONTINUE = "rf:intro:continue"
CB_INTRO_SKIP = "rf:intro:skip"
CB_INTRO_OPEN = "rf:intro:open"
CB_TEST_OK = "rf:test:ok"
CB_TEST_CHOOSE = "rf:test:choose"
CB_TEST_REGEN = "rf:test:regen"
CB_TEST_CUSTOM = "rf:test:custom"
CB_COVER_OK = "rf:cover:ok"
CB_COVER_REGEN = "rf:cover:regen"
CB_COVER_CUSTOM = "rf:cover:custom"
CB_CONFIRM_SEND = "rf:confirm:send"
CB_CONFIRM_SKIP = "rf:confirm:skip"

TARGET_TEST = "test_answer"
TARGET_COVER = "cover_letter"

# Имена состояний FSM.
STATE_IDLE = "idle"
STATE_REVIEW_INTRO = "review_intro"
STATE_REVIEW_TEST = "review_test_answer"
STATE_AWAIT_TEST_REGEN = "awaiting_test_regen_comment"
STATE_AWAIT_TEST_CUSTOM = "awaiting_custom_test_answer"
STATE_REVIEW_COVER = "review_cover_letter"
STATE_AWAIT_COVER_REGEN = "awaiting_cover_letter_regen_comment"
STATE_AWAIT_COVER_CUSTOM = "awaiting_custom_cover_letter"
STATE_CONFIRM_APPLY = "confirm_apply"

_NO_DRAFTS_MSG = (
    "Готово к ревью: 0 вакансий.\n"
    "Запустите prepare-vacancies, чтобы наполнить очередь."
)

# Состояния «ждущие текст» (маршрутизация в process_message).
_TEXT_INPUT_STATES: frozenset[str] = frozenset(
    {
        STATE_AWAIT_TEST_REGEN,
        STATE_AWAIT_TEST_CUSTOM,
        STATE_AWAIT_COVER_REGEN,
        STATE_AWAIT_COVER_CUSTOM,
    }
)


# ─── DTO ────────────────────────────────────────────────────────────

# InlineButton and OutgoingMessage are imported at the top of this
# module from job_bot.telegram_bot.models.message (VSA single source
# of truth, issue #87). The legacy review_flow.py shim re-exports
# them so existing imports keep working.

# ─── Сервис ─────────────────────────────────────────────────────────


class ReviewFlowService:
    """FSM интерактивного ревью черновиков в Telegram (issue #9)."""

    def __init__(
        self,
        storage: StorageFacade,
        transport: TelegramTransport,
        config: Mapping[str, Any] | None = None,
        *,
        clock: Clock | None = None,
        ai_client: AIClientPort | None = None,
    ):
        self._storage = storage
        self._transport = transport
        self._config: Mapping[str, Any] = config if config is not None else {}
        self._clock: Clock = (
            clock if clock is not None else self._default_clock()
        )
        self._ai_client = ai_client

    @staticmethod
    def _default_clock() -> Clock:
        # Ленивый импорт — избегаем цикла services → infrastructure.
        # ``SystemClock`` живёт в ``job_bot.shared.utils.clock``
        # ``SystemClock`` живёт в ``job_bot.shared.utils.clock`` (issue #153).
        from job_bot.shared.utils.clock import SystemClock

        return SystemClock()

    @property
    def clock(self) -> Clock:
        return self._clock

    @property
    def storage(self) -> StorageFacade:
        return self._storage

    # ─── Точки входа ─────────────────────────────────────────────

    def process_message(self, update: dict[str, Any]) -> list[OutgoingMessage]:
        """Обрабатывает текстовое сообщение от пользователя.

        Текст имеет смысл только в ``awaiting_*``-состояниях; в idle
        без draft (например, ``/start``) — подхватываем следующий
        ``prepared``-draft.
        """
        chat_id = _extract_chat_id(update)
        if chat_id is None:
            return []
        text = (update.get("message") or {}).get("text") or ""
        session = self._get_or_create_session(chat_id)
        state = session.state
        if state == STATE_AWAIT_TEST_REGEN:
            return self._handle_regen_comment(
                chat_id, session, text, target=TARGET_TEST
            )
        if state == STATE_AWAIT_COVER_REGEN:
            return self._handle_regen_comment(
                chat_id, session, text, target=TARGET_COVER
            )
        if state == STATE_AWAIT_TEST_CUSTOM:
            return self._handle_custom_answer(chat_id, session, text)
        if state == STATE_AWAIT_COVER_CUSTOM:
            return self._handle_custom_cover_letter(chat_id, session, text)
        if state == STATE_IDLE and session.draft_id is None:
            return self._load_next_draft(chat_id, session)
        return []

    def process_callback(self, update: dict[str, Any]) -> list[OutgoingMessage]:
        """Обрабатывает нажатие inline-кнопки (callback_query)."""
        chat_id = _extract_chat_id(update)
        if chat_id is None:
            return []
        callback = (update.get("callback_query") or {}).get("data") or ""
        if not callback.startswith("rf:"):
            return []
        session = self._get_or_create_session(chat_id)
        return self._dispatch_callback(chat_id, session, callback)

    def resume_session(self, chat_id: int) -> list[OutgoingMessage]:
        """Восстанавливает FSM для ``chat_id`` (вызывается при старте бота).

        idle без draft → берём следующий ``prepared``-draft; иначе —
        перерисовываем текущий шаг.
        """
        session = self._get_or_create_session(chat_id)
        if session.state == STATE_IDLE and session.draft_id is None:
            return self._load_next_draft(chat_id, session)
        return self._render_current_state(chat_id, session)

    # ─── Маршрутизация callback'ов ───────────────────────────────

    def _dispatch_callback(
        self, chat_id: int, session: TelegramSessionModel, callback: str
    ) -> list[OutgoingMessage]:
        """Двухуровневая маршрутизация: сначала state, внутри — action.

        Глобальные действия (skip / open) работают из любого шага.
        """
        state = session.state
        if callback == CB_INTRO_SKIP:
            return self._skip_current(chat_id, session)
        if callback == CB_CONFIRM_SKIP and state == STATE_CONFIRM_APPLY:
            return self._skip_current(chat_id, session)
        if callback == CB_INTRO_OPEN and state == STATE_REVIEW_INTRO:
            return self._handle_open_url(chat_id, session)
        if callback == CB_CONFIRM_SEND and state == STATE_CONFIRM_APPLY:
            return self._confirm_send(chat_id, session)
        if state == STATE_REVIEW_INTRO and callback == CB_INTRO_CONTINUE:
            return self._intro_continue(chat_id, session)

        # Таблица «action → handler» для каждого state.
        # Это естественное отражение «таблицы переходов» FSM.
        handlers: dict[tuple[str, str], Callable[[], list[OutgoingMessage]]] = {
            # review_test_answer
            (STATE_REVIEW_TEST, CB_TEST_OK): lambda: self._test_ok(
                chat_id, session
            ),
            (
                STATE_REVIEW_TEST,
                CB_TEST_CHOOSE,
            ): lambda: self._test_choose_other(chat_id, session),
            (STATE_REVIEW_TEST, CB_TEST_REGEN): lambda: self._enter_regen_state(
                chat_id, session, TARGET_TEST
            ),
            (
                STATE_REVIEW_TEST,
                CB_TEST_CUSTOM,
            ): lambda: self._enter_custom_answer_state(chat_id, session),
            # review_cover_letter
            (STATE_REVIEW_COVER, CB_COVER_OK): lambda: self._cover_ok(
                chat_id, session
            ),
            (
                STATE_REVIEW_COVER,
                CB_COVER_REGEN,
            ): lambda: self._enter_regen_state(chat_id, session, TARGET_COVER),
            (
                STATE_REVIEW_COVER,
                CB_COVER_CUSTOM,
            ): lambda: self._enter_custom_cover_state(chat_id, session),
        }
        handler = handlers.get((state, callback))
        return handler() if handler else []

    # ─── Загрузка следующего черновика ───────────────────────────

    def _load_next_draft(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        """Берёт FIFO-черновик (``prepared``) и переводит FSM в
        ``review_intro``. Если таких нет — отправляет уведомление
        и остаётся в ``idle``.
        """
        next_draft = self._next_prepared_draft()
        if next_draft is None:
            self._reset_session_to_idle(session)
            return [OutgoingMessage(chat_id=chat_id, text=_NO_DRAFTS_MSG)]
        # ``next_draft`` is a row fetched from the DB, so ``id`` is set.
        draft_id = cast(int, next_draft.id)
        self._bind_session_to_draft(session, draft_id, STATE_REVIEW_INTRO)
        return [self._render_intro(chat_id, next_draft)]

    def _next_prepared_draft(self) -> ApplicationDraftModel | None:
        """Самый старый ``prepared``-черновик (FIFO).

        Репозиторий сортирует по ``rowid DESC``; разворачиваем, чтобы
        бот не «залипал» на свежих вакансиях, пока висят старые.
        """
        drafts = list(self._storage.application_drafts.find(status="prepared"))
        return list(reversed(drafts))[0] if drafts else None

    # ─── Шаг 1: intro ───────────────────────────────────────────

    def _intro_continue(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None:
            return []
        if draft.has_test:
            # ``draft`` is DB-loaded, ``id`` is set.
            draft_id = cast(int, draft.id)
            first_answer = self._first_pending_test_answer(draft_id)
            if first_answer is not None:
                session.current_test_answer_id = first_answer.id
                self._set_state(session, STATE_REVIEW_TEST)
                return [self._render_test_answer(chat_id, draft, first_answer)]
            # has_test=True, но записей тестов нет — пропускаем шаг.
        self._set_state(session, STATE_REVIEW_COVER)
        return [self._render_cover_letter(chat_id, draft)]

    def _handle_open_url(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        """Кнопка «Открыть на HH»: шлёт ссылку отдельным сообщением,
        оставляя текущий шаг без изменений.
        """
        draft = self._require_draft(session)
        if draft is None:
            return []
        url = (draft.full_vacancy_json or {}).get("alternate_url") or (
            draft.hh_response_url
        )
        text = f"🔗 {url}" if url else "Ссылка на вакансию недоступна."
        return [OutgoingMessage(chat_id=chat_id, text=text)]

    # ─── Шаг 2: test answers ────────────────────────────────────

    def _test_ok(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None or session.current_test_answer_id is None:
            return []
        answer = self._storage.application_test_answers.get(
            session.current_test_answer_id
        )
        if answer is None:
            return self._advance_after_test_answer(chat_id, draft, session)
        answer.review_status = "approved"
        self._storage.application_test_answers.save(answer)
        self._commit()
        return self._advance_after_test_answer(chat_id, draft, session)

    def _advance_after_test_answer(
        self,
        chat_id: int,
        draft: ApplicationDraftModel,
        session: TelegramSessionModel,
    ) -> list[OutgoingMessage]:
        """Ищем следующий ещё не рассмотренный ответ (``review_status``
        не в ``('approved', 'custom')``). Если таких нет — cover.
        """
        # ``draft`` is DB-loaded, ``id`` is set.
        draft_id = cast(int, draft.id)
        for ans in self._storage.application_test_answers.find_by_draft(
            draft_id
        ):
            if ans.review_status not in ("approved", "custom"):
                session.current_test_answer_id = ans.id
                self._set_state(session, STATE_REVIEW_TEST)
                return [self._render_test_answer(chat_id, draft, ans)]
        session.current_test_answer_id = None
        self._set_state(session, STATE_REVIEW_COVER)
        return [self._render_cover_letter(chat_id, draft)]

    def _test_choose_other(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        """Циклически переключает ``selected_solution_id`` на следующий
        вариант из ``options_json`` (только для choice)."""
        draft = self._require_draft(session)
        if draft is None or session.current_test_answer_id is None:
            return []
        answer = self._storage.application_test_answers.get(
            session.current_test_answer_id
        )
        if answer is None:
            return []
        options = list(answer.options_json or [])
        if not options:
            return [self._render_test_answer(chat_id, draft, answer)]
        try:
            cur_idx = options.index(answer.generated_answer or "")
        except ValueError:
            cur_idx = -1
        new_value = options[(cur_idx + 1) % len(options)]
        answer.generated_answer = new_value
        answer.selected_solution_id = new_value
        answer.review_status = "needs_choice"
        self._storage.application_test_answers.save(answer)
        self._commit()
        return [self._render_test_answer(chat_id, draft, answer)]

    def _enter_regen_state(
        self, chat_id: int, session: TelegramSessionModel, target: str
    ) -> list[OutgoingMessage]:
        session.payload_json = {"target": target}
        new_state = (
            STATE_AWAIT_TEST_REGEN
            if target == TARGET_TEST
            else STATE_AWAIT_COVER_REGEN
        )
        self._set_state(session, new_state)
        return [
            OutgoingMessage(
                chat_id=chat_id,
                text=(
                    "✏️ Напишите комментарий или отправьте `-` для "
                    "перегенерации без изменений."
                ),
            )
        ]

    def _enter_custom_answer_state(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        session.payload_json = None
        self._set_state(session, STATE_AWAIT_TEST_CUSTOM)
        return [
            OutgoingMessage(
                chat_id=chat_id,
                text="✏️ Отправьте свой ответ на этот вопрос.",
            )
        ]

    def _handle_regen_comment(
        self,
        chat_id: int,
        session: TelegramSessionModel,
        text: str,
        *,
        target: str,
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None:
            return []
        if self._ai_client is None:
            return [
                OutgoingMessage(
                    chat_id=chat_id,
                    text="⚠️ AI не настроен. Попробуйте «Свой ответ».",
                )
            ]
        if target == TARGET_TEST:
            return self._apply_test_regen(chat_id, session, text, draft)
        return self._apply_cover_regen(chat_id, session, text, draft)

    def _apply_test_regen(
        self,
        chat_id: int,
        session: TelegramSessionModel,
        text: str,
        draft: ApplicationDraftModel,
    ) -> list[OutgoingMessage]:
        if session.current_test_answer_id is None:
            return []
        answer = self._storage.application_test_answers.get(
            session.current_test_answer_id
        )
        if answer is None:
            return []
        new_value = self._call_ai_for_test(answer, text)
        answer.generated_answer = new_value
        if answer.answer_type == "choice":
            answer.selected_solution_id = new_value
        answer.review_status = "regenerated"
        answer.reviewer_comment = text
        self._storage.application_test_answers.save(answer)
        self._commit()
        session.payload_json = None
        self._set_state(session, STATE_REVIEW_TEST)
        return [self._render_test_answer(chat_id, draft, answer)]

    def _apply_cover_regen(
        self,
        chat_id: int,
        session: TelegramSessionModel,
        text: str,
        draft: ApplicationDraftModel,
    ) -> list[OutgoingMessage]:
        draft.cover_letter = self._call_ai_for_cover(draft, text)
        draft.cover_letter_status = "regenerated"
        self._storage.application_drafts.save(draft)
        self._commit()
        session.payload_json = None
        self._set_state(session, STATE_REVIEW_COVER)
        return [self._render_cover_letter(chat_id, draft)]

    def _handle_custom_answer(
        self,
        chat_id: int,
        session: TelegramSessionModel,
        text: str,
    ) -> list[OutgoingMessage]:
        if session.current_test_answer_id is None:
            return []
        answer = self._storage.application_test_answers.get(
            session.current_test_answer_id
        )
        if answer is None:
            return []
        answer.generated_answer = text
        answer.review_status = "custom"
        self._storage.application_test_answers.save(answer)
        self._commit()
        draft = self._require_draft(session)
        if draft is None:
            return []
        return self._advance_after_test_answer(chat_id, draft, session)

    # ─── Шаг 3: cover letter ────────────────────────────────────

    def _cover_ok(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None:
            return []
        draft.cover_letter_status = "approved"
        self._storage.application_drafts.save(draft)
        self._commit()
        session.payload_json = None
        self._set_state(session, STATE_CONFIRM_APPLY)
        return [self._render_confirm(chat_id, draft)]

    def _enter_custom_cover_state(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        session.payload_json = None
        self._set_state(session, STATE_AWAIT_COVER_CUSTOM)
        return [
            OutgoingMessage(
                chat_id=chat_id,
                text="✏️ Отправьте свой текст сопроводительного письма.",
            )
        ]

    def _handle_custom_cover_letter(
        self,
        chat_id: int,
        session: TelegramSessionModel,
        text: str,
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None:
            return []
        draft.cover_letter = text
        draft.cover_letter_status = "custom"
        self._storage.application_drafts.save(draft)
        self._commit()
        session.payload_json = None
        self._set_state(session, STATE_CONFIRM_APPLY)
        return [self._render_confirm(chat_id, draft)]

    # ─── Шаг 4: confirm ─────────────────────────────────────────

    def _confirm_send(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None:
            return []
        draft.status = "queued"
        self._storage.application_drafts.save(draft)
        # Один job на черновик — UPSERT на draft_id.
        # Сохраняем chat_id для пер-драфтовых уведомлений (issue #43).
        self._storage.apply_jobs.save(
            # ``draft`` is DB-loaded, ``id`` is set.
            ApplyJobModel(
                draft_id=cast(int, draft.id),
                status="queued",
                chat_id=chat_id,
            )
        )
        self._commit()
        ack = OutgoingMessage(
            chat_id=chat_id,
            text=(
                "✅ Отклик поставлен в очередь. Перехожу к следующей вакансии."
            ),
        )
        self._reset_session_to_idle(session)
        return [ack, *self._load_next_draft(chat_id, session)]

    # ─── Skip (любой шаг) ──────────────────────────────────────

    def _skip_current(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        draft = self._require_draft(session)
        if draft is None:
            return []
        draft.status = "skipped"
        self._storage.application_drafts.save(draft)
        self._commit()
        self._reset_session_to_idle(session)
        return [
            OutgoingMessage(chat_id=chat_id, text="⏭️ Черновик пропущен."),
            *self._load_next_draft(chat_id, session),
        ]

    # ─── Рендеринг (для resume + повторных показов) ─────────────

    def _render_current_state(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        state = session.state
        if state in _TEXT_INPUT_STATES:
            return [
                OutgoingMessage(
                    chat_id=chat_id,
                    text="✏️ Ожидаю ваш ввод (продолжаем с прошлого шага).",
                )
            ]
        if state == STATE_REVIEW_TEST:
            return self._render_review_test(chat_id, session)
        # Для остальных состояний сначала ищем draft; если потерян —
        # пробуем загрузить следующий (пользователь не застрянет в тишине).
        renderer: dict[str, Any] = {
            STATE_REVIEW_INTRO: self._render_intro,
            STATE_REVIEW_COVER: self._render_cover_letter,
            STATE_CONFIRM_APPLY: self._render_confirm,
        }
        if state in renderer:
            draft = self._require_draft(session)
            if draft is None:
                return self._load_next_draft(chat_id, session)
            return [renderer[state](chat_id, draft)]
        return [OutgoingMessage(chat_id=chat_id, text="(нет активного ревью)")]

    def _render_review_test(
        self, chat_id: int, session: TelegramSessionModel
    ) -> list[OutgoingMessage]:
        if session.current_test_answer_id is None:
            return []
        draft = self._require_draft(session)
        if draft is None:
            return []
        answer = self._storage.application_test_answers.get(
            session.current_test_answer_id
        )
        if answer is None:
            return self._advance_after_test_answer(chat_id, draft, session)
        return [self._render_test_answer(chat_id, draft, answer)]

    def _render_intro(
        self, chat_id: int, draft: ApplicationDraftModel
    ) -> OutgoingMessage:
        return OutgoingMessage(
            chat_id=chat_id,
            text=self._format_intro(draft),
            reply_markup=[
                [InlineButton("Продолжить", CB_INTRO_CONTINUE)],
                [InlineButton("Пропустить", CB_INTRO_SKIP)],
                [InlineButton("Открыть на HH", CB_INTRO_OPEN)],
            ],
        )

    def _render_test_answer(
        self,
        chat_id: int,
        draft: ApplicationDraftModel,
        answer: ApplicationTestAnswerModel,
    ) -> OutgoingMessage:
        if answer.answer_type == "choice":
            buttons = [
                [InlineButton("Ок", CB_TEST_OK)],
                [InlineButton("Выбрать другой", CB_TEST_CHOOSE)],
                [InlineButton("Перегенерировать", CB_TEST_REGEN)],
                [InlineButton("Свой ответ", CB_TEST_CUSTOM)],
            ]
        else:
            buttons = [
                [InlineButton("Ок", CB_TEST_OK)],
                [InlineButton("Перегенерировать", CB_TEST_REGEN)],
                [InlineButton("Свой ответ", CB_TEST_CUSTOM)],
            ]
        return OutgoingMessage(
            chat_id=chat_id,
            text=self._format_test_answer(draft, answer),
            reply_markup=buttons,
        )

    def _render_cover_letter(
        self, chat_id: int, draft: ApplicationDraftModel
    ) -> OutgoingMessage:
        return OutgoingMessage(
            chat_id=chat_id,
            text=self._format_cover_letter(draft),
            reply_markup=[
                [InlineButton("Ок", CB_COVER_OK)],
                [InlineButton("Перегенерировать", CB_COVER_REGEN)],
                [InlineButton("Свой ответ", CB_COVER_CUSTOM)],
            ],
        )

    def _render_confirm(
        self, chat_id: int, draft: ApplicationDraftModel
    ) -> OutgoingMessage:
        return OutgoingMessage(
            chat_id=chat_id,
            text=self._format_confirm(draft),
            reply_markup=[
                [InlineButton("Отправить", CB_CONFIRM_SEND)],
                [InlineButton("Пропустить", CB_CONFIRM_SKIP)],
            ],
        )

    # ─── Форматирование текста ─────────────────────────────────

    def _format_intro(self, draft: ApplicationDraftModel) -> str:
        full = draft.full_vacancy_json or {}
        name = str(full.get("name") or "(без названия)")
        employer = (full.get("employer") or {}).get(
            "name"
        ) or "(компания не указана)"
        url = full.get("alternate_url") or draft.hh_response_url or ""
        analysis = draft.analysis_json or {}
        lines = [
            f"📌 *{name}*",
            f"Компания: {employer}",
            f"Оклад: {_format_salary(full.get('salary'))}",
            f"Формат: {_format_schedule(full)}",
        ]
        if url:
            lines.append(f"Ссылка: {url}")
        lines.append("")
        lines.append(
            f"Стэк: {', '.join(analysis.get('primary_stack') or []) or '—'}"
        )
        lines.append(f"Проект: {analysis.get('project') or '—'}")
        lines.append(f"Сложность: {analysis.get('complexity') or '—'}")
        if draft.relevance_score is not None:
            lines.append(f"Релевантность: {draft.relevance_score}")
        if draft.success_probability is not None:
            lines.append(f"Прогноз успеха: {draft.success_probability}%")
        lines.append(f"Причина: {draft.relevance_reason or '—'}")
        lines.append(f"Риски: {', '.join(analysis.get('risks') or []) or '—'}")
        return "\n".join(lines)

    def _format_test_answer(
        self,
        draft: ApplicationDraftModel,
        answer: ApplicationTestAnswerModel,
    ) -> str:
        # N/M берём из фактического списка ответов в БД (для
        # черновика обычно 1-3, лишний SELECT не страшен).
        # ``draft`` is DB-loaded, ``id`` is set.
        draft_id = cast(int, draft.id)
        all_ids = [
            a.id
            for a in self._storage.application_test_answers.find_by_draft(
                draft_id
            )
        ]
        total = len(all_ids)
        try:
            idx = all_ids.index(answer.id) + 1
        except ValueError:
            idx = 1
        lines = [f"Вопрос {idx}/{total}:"]
        if answer.question:
            lines.append(answer.question)
        if answer.answer_type == "choice" and answer.options_json:
            options = " / ".join(str(o) for o in answer.options_json)
            lines.append(f"Варианты: {options}")
            lines.append(f"Выбранный ответ: {answer.generated_answer or '—'}")
        else:
            lines.append("Сгенерированный ответ:")
            lines.append(answer.generated_answer or "(пусто)")
        return "\n\n".join(lines)

    @staticmethod
    def _format_cover_letter(draft: ApplicationDraftModel) -> str:
        full = draft.full_vacancy_json or {}
        name = str(full.get("name") or "(без названия)")
        return (
            f"✉️ *Сопроводительное письмо* (вакансия: {name})\n\n"
            f"{draft.cover_letter or '(пусто)'}"
        )

    @staticmethod
    def _format_confirm(draft: ApplicationDraftModel) -> str:
        full = draft.full_vacancy_json or {}
        name = str(full.get("name") or "(без названия)")
        employer = (full.get("employer") or {}).get(
            "name"
        ) or "(компания не указана)"
        return (
            "📤 *Отправить отклик?*\n\n"
            f"Вакансия: {name}\n"
            f"Компания: {employer}\n"
            f"Письмо: {'есть' if draft.cover_letter else 'нет'}\n"
            f"Тесты: {'есть' if draft.has_test else 'нет'}"
        )

    # ─── AI-вызовы (регенерация) ───────────────────────────────

    def _call_ai_for_test(
        self, answer: ApplicationTestAnswerModel, comment: str
    ) -> str:
        assert self._ai_client is not None
        prompt = self._build_test_regen_prompt(answer, comment)
        return self._ai_client.complete(prompt).strip()

    def _call_ai_for_cover(
        self, draft: ApplicationDraftModel, comment: str
    ) -> str:
        assert self._ai_client is not None
        prompt = self._build_cover_regen_prompt(draft, comment)
        return self._ai_client.complete(prompt).strip()

    @staticmethod
    def _build_test_regen_prompt(
        answer: ApplicationTestAnswerModel, comment: str
    ) -> str:
        header = (
            "Перегенерируй ответ на вопрос теста HH. "
            "Верни только текст ответа, без пояснений."
        )
        body = [
            f"Вопрос: {answer.question or ''}",
            f"Тип: {answer.answer_type or 'text'}",
        ]
        if answer.options_json:
            body.append(
                "Варианты: " + ", ".join(str(o) for o in answer.options_json)
            )
        body.append(f"Текущий ответ: {answer.generated_answer or ''}")
        body.append(
            f"Комментарий пользователя: {comment or '(без комментария)'}"
        )
        return header + "\n" + "\n".join(body)

    @staticmethod
    def _build_cover_regen_prompt(
        draft: ApplicationDraftModel, comment: str
    ) -> str:
        full = draft.full_vacancy_json or {}
        return (
            "Перепиши сопроводительное письмо для вакансии. "
            "Верни только текст письма, без пояснений.\n"
            f"Вакансия: {full.get('name') or ''}\n"
            f"Компания: {(full.get('employer') or {}).get('name') or ''}\n"
            f"Текущее письмо: {draft.cover_letter or ''}\n"
            f"Комментарий пользователя: {comment or '(без комментария)'}"
        )

    # ─── Сессия: чтение/запись / утилиты состояния ─────────────

    def _get_or_create_session(self, chat_id: int) -> TelegramSessionModel:
        session = self._storage.telegram_sessions.get(chat_id)
        if session is None:
            session = TelegramSessionModel(chat_id=chat_id, state=STATE_IDLE)
            self._storage.telegram_sessions.save(session)
            self._commit()
        # ``session`` is narrowed to ``TelegramSessionModel`` after the
        # ``if session is None`` branch; ``cast`` re-asserts this for mypy.
        return cast(TelegramSessionModel, session)

    def _save_session(self, session: TelegramSessionModel) -> None:
        session.updated_at = _iso_now(self._clock)
        self._storage.telegram_sessions.save(session)
        self._commit()

    def _set_state(
        self,
        session: TelegramSessionModel,
        state: str,
        *,
        draft_id: int | None = None,
        clear_draft: bool = False,
    ) -> None:
        """Единая мутация FSM: меняет ``state`` + (опц.) привязывает/отвязывает черновик.

        - ``draft_id`` задан → привязываем сессию к этому черновику;
        - ``clear_draft=True`` → отвязываем;
        - иначе — только меняем ``state``. При смене draft сбрасываем
          ``current_test_answer_id`` и ``payload_json``.
        """
        session.state = state
        if draft_id is not None or clear_draft:
            session.draft_id = draft_id
            session.current_test_answer_id = None
            session.payload_json = None
        self._save_session(session)

    def _reset_session_to_idle(self, session: TelegramSessionModel) -> None:
        """Сбрасывает сессию в ``idle`` без привязки к черновику
        (используется при skip / confirm_send / потере черновика)."""
        self._set_state(session, STATE_IDLE, clear_draft=True)

    def _bind_session_to_draft(
        self,
        session: TelegramSessionModel,
        draft_id: int,
        state: str,
    ) -> None:
        """Привязывает сессию к конкретному черновику и переводит в
        ``state``. Используется при загрузке следующего черновика."""
        self._set_state(session, state, draft_id=draft_id)

    def _require_draft(
        self, session: TelegramSessionModel
    ) -> ApplicationDraftModel | None:
        if session.draft_id is None:
            return None
        draft = self._storage.application_drafts.get(session.draft_id)
        if draft is None:
            # Потеряли черновик — сбрасываем сессию в idle.
            self._reset_session_to_idle(session)
        # ``draft`` is either a ``ApplicationDraftModel`` or ``None``
        # (when the row was missing); the cast re-asserts the return type.
        return cast(ApplicationDraftModel | None, draft)

    def _first_pending_test_answer(
        self, draft_id: int
    ) -> ApplicationTestAnswerModel | None:
        """Первый «ещё не рассмотренный» ответ теста для черновика."""
        for ans in self._storage.application_test_answers.find_by_draft(
            draft_id
        ):
            if ans.review_status not in ("approved", "custom"):
                return ans
        return None

    def _commit(self) -> None:
        """Обёртка над ``commit`` для всех мутаций FSM.

        Все репозитории шарят одно соединение (см. ``StorageFacade``),
        поэтому единый ``commit`` достаточно для любой операции.
        """
        conn = self._storage.application_drafts.conn
        if conn.in_transaction:
            conn.commit()

    def send(self, messages: list[OutgoingMessage]) -> list[dict[str, Any]]:
        """Отправляет список DTO через :attr:`_transport`.

        В unit-тестах FSM мы работаем с DTO напрямую и не вызываем
        этот метод (Telegram API закрыт моком).
        """
        results: list[dict[str, Any]] = []
        for msg in messages:
            # Сейчас transport.send_message принимает только text. Кнопки
            # отправляются отдельным вызовом при подключении бота — здесь
            # оставлен задел для будущего расширения API транспорта.
            results.append(self._transport.send_message(msg.chat_id, msg.text))
        return results


# ─── Утилиты модуля (не часть публичного API) ───────────────────────


def _extract_chat_id(update: dict[str, Any]) -> int | None:
    """Достаёт ``chat_id`` из Telegram-апдейта (message / callback_query)."""
    msg = update.get("message") or {}
    if "chat" in msg and "id" in msg["chat"]:
        return int(msg["chat"]["id"])
    cb = update.get("callback_query") or {}
    if "message" in cb and "chat" in cb["message"]:
        return int(cb["message"]["chat"]["id"])
    return None


def _iso_now(clock: Clock) -> str:
    """ISO-формат текущего времени для ``updated_at`` сессии."""
    return clock.now().isoformat(sep=" ", timespec="seconds")


def _format_salary(salary: Any) -> str:
    if not isinstance(salary, Mapping):
        return "не указан"
    s_from = salary.get("from")
    s_to = salary.get("to")
    currency = salary.get("currency") or "RUR"
    if s_from and s_to:
        return f"{s_from:,} – {s_to:,} {currency}".replace(",", " ")
    if s_from:
        return f"от {s_from:,} {currency}".replace(",", " ")
    if s_to:
        return f"до {s_to:,} {currency}".replace(",", " ")
    return "не указан"


def _format_schedule(full: Mapping[str, Any]) -> str:
    parts: list[str] = []
    schedule = full.get("schedule")
    if isinstance(schedule, Mapping):
        name = schedule.get("name")
        if name:
            parts.append(str(name))
    employment = full.get("employment")
    if isinstance(employment, Mapping):
        name = employment.get("name")
        if name:
            parts.append(str(name))
    return ", ".join(parts) if parts else "не указан"


__all__ = (
    "InlineButton",
    "OutgoingMessage",
    "ReviewFlowService",
    "CB_INTRO_CONTINUE",
    "CB_INTRO_SKIP",
    "CB_INTRO_OPEN",
    "CB_TEST_OK",
    "CB_TEST_CHOOSE",
    "CB_TEST_REGEN",
    "CB_TEST_CUSTOM",
    "CB_COVER_OK",
    "CB_COVER_REGEN",
    "CB_COVER_CUSTOM",
    "CB_CONFIRM_SEND",
    "CB_CONFIRM_SKIP",
    "STATE_IDLE",
    "STATE_REVIEW_INTRO",
    "STATE_REVIEW_TEST",
    "STATE_AWAIT_TEST_REGEN",
    "STATE_AWAIT_TEST_CUSTOM",
    "STATE_REVIEW_COVER",
    "STATE_AWAIT_COVER_REGEN",
    "STATE_AWAIT_COVER_CUSTOM",
    "STATE_CONFIRM_APPLY",
)
