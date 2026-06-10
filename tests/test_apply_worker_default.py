"""Тесты дефолтной реализации ``make_default_apply_one`` (issue #10).

Покрывает классификацию ошибок, которую делает дефолтная обёртка
над ``api_client.post("/negotiations", ...)``:
- 5xx / 429 / captcha / network → :class:`RetryableError`;
- 400 / 403 / 404 / ``has_test=True`` / ``response is None`` → :class:`FatalError`;
- успех → ``api_client.post`` с правильными параметрами.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hh_applicant_tool.services.apply_worker import (
    FatalError,
    RetryableError,
    make_default_apply_one,
)
from hh_applicant_tool.storage.models.application_draft import (
    ApplicationDraftModel,
)


def _make_response_api_error(status: int, message: str = "boom"):
    """Фейк :class:`ApiError` с заданным status_code."""
    from hh_applicant_tool.api.errors import ApiError

    fake_resp = MagicMock()
    fake_resp.status_code = status
    fake_resp.request = MagicMock()
    return ApiError(fake_resp, {"description": message})


def _queued_draft(**kwargs: object) -> ApplicationDraftModel:
    """Минимальный draft для тестов ``make_default_apply_one``."""
    return ApplicationDraftModel(
        resume_id="r1", vacancy_id=1, status="queued", **kwargs
    )


def test_5xx_is_retryable():
    """5xx от HH → RetryableError."""
    api_client = MagicMock()
    api_client.post.side_effect = _make_response_api_error(503, "down")
    with pytest.raises(RetryableError):
        make_default_apply_one(api_client)(_queued_draft())


def test_429_is_retryable():
    """429 rate-limit → RetryableError."""
    api_client = MagicMock()
    api_client.post.side_effect = _make_response_api_error(429, "slow down")
    with pytest.raises(RetryableError):
        make_default_apply_one(api_client)(_queued_draft())


def test_400_is_fatal():
    """400 bad request → FatalError (наш баг / устаревший черновик)."""
    api_client = MagicMock()
    api_client.post.side_effect = _make_response_api_error(400, "bad")
    with pytest.raises(FatalError):
        make_default_apply_one(api_client)(_queued_draft())


def test_403_is_fatal():
    """403 → FatalError."""
    api_client = MagicMock()
    api_client.post.side_effect = _make_response_api_error(403, "forbidden")
    with pytest.raises(FatalError):
        make_default_apply_one(api_client)(_queued_draft())


def test_captcha_is_retryable():
    """CaptchaRequired → RetryableError (можно повторить позже)."""
    from hh_applicant_tool.api.errors import CaptchaRequired

    api_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.status_code = 403
    fake_resp.request = MagicMock()
    api_client.post.side_effect = CaptchaRequired(
        fake_resp,
        {
            "errors": [
                {
                    "type": "captcha_required",
                    "value": "captcha_required",
                    "captcha_url": "https://hh.ru/captcha?x=1",
                }
            ]
        },
    )
    with pytest.raises(RetryableError) as exc:
        make_default_apply_one(api_client)(_queued_draft())
    assert "captcha" in str(exc.value).lower()


def test_limit_exceeded_is_retryable():
    """LimitExceeded → RetryableError."""
    from hh_applicant_tool.api.errors import LimitExceeded

    api_client = MagicMock()
    fake_resp = MagicMock()
    fake_resp.status_code = 400
    fake_resp.request = MagicMock()
    api_client.post.side_effect = LimitExceeded(
        fake_resp,
        {
            "errors": [
                {"type": "limit", "value": "limit_exceeded"},
            ]
        },
    )
    with pytest.raises(RetryableError):
        make_default_apply_one(api_client)(_queued_draft())


def test_request_exception_is_retryable():
    """requests.RequestException → RetryableError (сеть)."""
    from requests import ConnectionError

    api_client = MagicMock()
    api_client.post.side_effect = ConnectionError("dns fail")
    with pytest.raises(RetryableError):
        make_default_apply_one(api_client)(_queued_draft())


def test_sends_correct_params():
    """Успех: api_client.post вызван с ``/negotiations`` и нужными полями."""
    api_client = MagicMock()
    api_client.post.return_value = {}
    make_default_apply_one(api_client)(
        ApplicationDraftModel(
            resume_id="r-42",
            vacancy_id=123,
            status="queued",
            cover_letter="Здравствуйте!",
        )
    )
    api_client.post.assert_called_once()
    args, _ = api_client.post.call_args
    assert args[0] == "/negotiations"
    params = args[1]
    assert params["resume_id"] == "r-42"
    assert params["vacancy_id"] == "123"
    assert params["message"] == "Здравствуйте!"


def test_empty_cover_letter_sends_empty_message():
    """Пустой ``cover_letter`` → ``message=""`` (не падает)."""
    api_client = MagicMock()
    api_client.post.return_value = {}
    make_default_apply_one(api_client)(
        ApplicationDraftModel(
            resume_id="r-1", vacancy_id=1, status="queued", cover_letter=None
        )
    )
    params = api_client.post.call_args[0][1]
    assert params["message"] == ""


def test_none_response_is_fatal():
    """``api_client.post`` вернул ``None`` → FatalError (redirect/dry-run)."""
    api_client = MagicMock()
    api_client.post.return_value = None
    with pytest.raises(FatalError):
        make_default_apply_one(api_client)(_queued_draft())
