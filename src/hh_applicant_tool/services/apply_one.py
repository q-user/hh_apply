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


def make_default_apply_one(
    api_client: Any,
    *,
    session: Any | None = None,
    xsrf_token: str | None = None,
    ai_client: Any | None = None,
) -> "ApplyOneDraftFn":
    """Собирает дефолтный ``apply_one`` callable.

    Использует ``api_client.post("/negotiations", ...)`` по аналогии
    с :meth:`ApplyToVacanciesUseCase._send_apply_request`. Классифицирует
    ошибки: 5xx/429/captcha/network → :class:`RetryableError`,
    400/403/404 → :class:`FatalError`.

    Вакансии с тестами (``has_test=True``) → использует
    ``VacancyTestsService`` для прохождения теста и отправки отклика.

    Args:
        api_client: HTTP-клиент HH API (имеет атрибут ``session``).
        session: ``requests.Session`` для парсинга тестов и отправки ответов.
            Если не задан — используется ``api_client.session``.
        xsrf_token: XSRF-токен для отправки ответов на тесты.
            Если не задан — пытается извлечь из сессии.
        ai_client: AI-клиент для генерации ответов на тесты (опционально).
    """
    # Импортируем лениво, чтобы не циклить services ↔ api/errors.
    from requests import RequestException

    from ..api.errors import ApiError, CaptchaRequired, LimitExceeded
    from .apply_worker import FatalError, RetryableError
    from .vacancy_tests import VacancyTestsService

    def _get_xsrf_token(sess: Any) -> str:
        """Извлекает XSRF-токен из HTML главной страницы hh.ru."""
        xsrf_token_marker = ',"xsrfToken":"'
        r = sess.get("https://hh.ru/")
        s1 = r.text.find(xsrf_token_marker)
        if s1 == -1:
            raise FatalError("xsrf token not found in session")
        s1 += len(xsrf_token_marker)
        s2 = r.text.find('"', s1)
        if s2 == -1:
            raise FatalError("malformed xsrf token")
        return r.text[s1:s2]

    def _apply_one(draft: ApplicationDraftModel) -> None:
        if draft.has_test:
            # Реализация apply_with_test через VacancyTestsService
            sess = session or getattr(api_client, "session", None)
            if sess is None:
                raise FatalError(
                    "no HTTP session available for test application"
                )

            # Получить xsrf_token
            token = xsrf_token or _get_xsrf_token(sess)

            # URL страницы отклика на вакансию
            response_url = f"https://hh.ru/applicant/vacancy_response?vacancy_id={draft.vacancy_id}"

            # Создать сервис тестов
            test_service = VacancyTestsService(
                session=sess,
                ai_client=ai_client,
            )

            try:
                # 1. Загрузить данные тестов
                test_data = test_service.fetch_tests(response_url)
            except ValueError as ex:
                raise FatalError(f"failed to fetch tests: {ex}") from ex

            try:
                # 2. Подготовить ответы
                answers = test_service.prepare_answers(test_data)
            except Exception as ex:  # noqa: BLE001
                raise FatalError(
                    f"failed to prepare test answers: {ex}"
                ) from ex

            # 3. Построить payload для отправки
            resume_hash = draft.resume_id  # используем resume_id как hash
            letter = draft.cover_letter or ""
            payload = test_service.build_apply_payload_from_answers(
                test_data=test_data,
                answers=answers,
                vacancy_id=draft.vacancy_id,
                resume_hash=resume_hash,
                letter=letter,
                xsrf_token=token,
            )

            try:
                # 4. Отправить отклик с ответами на тесты
                result = test_service.submit_apply(
                    response_url, payload, xsrf_token=token
                )
            except RequestException as ex:
                raise RetryableError(
                    f"network error submitting test apply: {ex}"
                ) from ex
            except Exception as ex:  # noqa: BLE001
                raise FatalError(f"failed to submit test apply: {ex}") from ex

            # Проверить результат
            if not result.get("success", False):
                error_msg = (
                    result.get("error")
                    or result.get("message")
                    or "unknown error"
                )
                raise FatalError(f"test apply failed: {error_msg}")

            return

        # Обычный отклик без теста
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
