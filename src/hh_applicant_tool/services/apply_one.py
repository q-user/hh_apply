"""Дефолтная реализация «apply one draft» для воркера (issue #10).

Вынесена из :mod:`hh_applicant_tool.services.apply_worker`, чтобы
не раздувать основной модуль сервиса. ``make_default_apply_one``
принимает ``api_client`` и возвращает callable, удовлетворяющий
:class:`ApplyOneDraftFn` (контракт воркера).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from ..storage.models.application_draft import ApplicationDraftModel

if TYPE_CHECKING:
    from .apply_worker import ApplyOneDraftFn


def make_default_apply_one(api_client: Any) -> "ApplyOneDraftFn":
    """Собирает дефолтный ``apply_one`` callable.

    Использует ``api_client.post("/negotiations", ...)`` по аналогии
    с :meth:`ApplyToVacanciesUseCase._send_apply_request`. Классифицирует
    ошибки: 5xx/429/captcha/network → :class:`RetryableError`,
    400/403/404 → :class:`FatalError`.

    Вакансии с тестами (``has_test=True``) → :class:`FatalError` (TODO
    follow-up: implement apply_with_test с reviewed ответами).
    """
    # Импортируем лениво, чтобы не циклить services ↔ api/errors.
    from requests import RequestException

    from ..api.errors import ApiError, CaptchaRequired, LimitExceeded
    from .apply_worker import FatalError, RetryableError

    def _apply_one(draft: ApplicationDraftModel) -> None:
        if draft.has_test:
            raise FatalError(
                "apply_with_test пока не реализован в воркере; "
                "пройдите тест вручную через Telegram-ревью"
            )

        params = {
            "resume_id": draft.resume_id,
            "vacancy_id": str(draft.vacancy_id),
            "message": draft.cover_letter or "",
        }
        try:
            response = api_client.post("/negotiations", params)
        except CaptchaRequired as ex:
            raise RetryableError(f"captcha required: {ex.captcha_url}") from ex
        except LimitExceeded as ex:
            raise RetryableError("hh limit exceeded") from ex
        except ApiError as ex:
            status = getattr(ex, "status_code", None)
            if status is not None and 500 <= status < 600:
                raise RetryableError(f"hh {status}: {ex.message}") from ex
            if status == 429:
                raise RetryableError("hh rate limited (429)") from ex
            raise FatalError(f"hh {status}: {ex.message}") from ex
        except RequestException as ex:
            raise RetryableError(f"network: {ex}") from ex
        except Exception as ex:  # noqa: BLE001
            raise RetryableError(f"unexpected: {ex!r}") from ex

        if response is None:
            # api_client может вернуть None при redirect/dry-run.
            raise FatalError("empty response from /negotiations")

    return _apply_one
