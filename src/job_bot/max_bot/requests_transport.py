"""``requests``-backed :class:`MaxTransportPort` implementation (issue #60).

Real MAX Bot API client targeting ``https://botapi.max.ru`` (the public
MAX Messenger Bot API). Two main methods:

* ``get_updates`` -- long-poll ``/updates`` (the API uses
  ``https://botapi.max.ru/updates?timeout=...&offset=...``).
* ``send_message`` -- POST ``/messages`` with
  ``{"chat_id": ..., "text": ...}``.

Both methods return data shaped exactly like the public API
response (``{"ok": true, "result": ...}`` style). On HTTP / network
failure the transport raises :class:`MaxTransportError` so the
:class:`TransportHandler` can apply its existing back-off loop (no
behaviour change in the polling code).

Rate limiting: the API returns HTTP 429 with a ``Retry-After`` header
on flood; we honour it by sleeping before raising so the caller can
retry without burning the back-off budget.
"""

from __future__ import annotations

import logging
import time
from typing import Any

logger = logging.getLogger(__name__)


# Back-off defaults for the polling stub.
_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 60.0

# Default MAX Bot API base URL (matches ``MaxSettings.api_url``).
DEFAULT_API_URL = "https://botapi.max.ru"

# Cap on how long we honour a single ``Retry-After`` value (in
# seconds) -- the API occasionally returns absurdly large values
# that would lock the polling loop for hours.
_MAX_RETRY_AFTER = 30.0


class MaxTransportError(RuntimeError):
    """Raised when the MAX Bot API returns an error or the network fails.

    Carries the original HTTP status code (when applicable) and a
    short error message for logging. The polling loop in
    :class:`TransportHandler` catches this via the generic
    ``except Exception`` branch and applies its exponential back-off.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


class RequestsMaxTransport:
    """A :class:`MaxTransportPort` over ``requests.Session`` (issue #60).

    Args:
        session: a ``requests.Session`` (typically the tool's shared
            session so cookies / proxy settings are inherited).
        bot_token: the MAX Bot API token. Sent as the
            ``Authorization`` header on every request.
        api_url: base URL of the MAX Bot API. Defaults to the public
            ``https://botapi.max.ru``.
        sleep_fn: optional override for the sleep helper (used by
            tests to bound ``Retry-After`` waits).
    """

    def __init__(
        self,
        *,
        session: Any,
        bot_token: str,
        api_url: str = DEFAULT_API_URL,
        sleep_fn: Any | None = None,
    ) -> None:
        if not bot_token:
            raise ValueError("bot_token is required for the MAX transport")
        self._session = session
        self._bot_token = bot_token
        self._api_url = api_url.rstrip("/")
        self._sleep = sleep_fn or time.sleep

    # ─── Public API ───────────────────────────────────────────────

    def get_updates(
        self,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        """Long-poll ``/updates`` and return the new updates list.

        Args:
            offset: identifier of the first update to return. The MAX
                API returns updates with ``update_id >= offset`` (or
                all updates when ``offset`` is ``None``).
            timeout: long-poll timeout in seconds (server-side hold
                time).

        Returns:
            List of update dicts. Empty list when the server has
            nothing new to deliver.
        """
        params: dict[str, Any] = {"timeout": max(0, int(timeout))}
        if offset is not None:
            params["offset"] = int(offset)

        payload = self._call("GET", "/updates", params=params)
        # MAX returns either a bare list or a {"updates": [...]} envelope
        # depending on the API version. Normalise to a list of updates.
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict) and "updates" in payload:
            return list(payload.get("updates") or [])
        if isinstance(payload, dict) and "result" in payload:
            return list(payload.get("result") or [])
        # Unknown shape: log and behave as "no updates" so the loop
        # stays alive.
        logger.warning(
            "MAX get_updates: unexpected payload shape %r -- treating as empty",
            type(payload).__name__,
        )
        return []

    def send_message(self, chat_id: int, text: str) -> bool:
        """POST ``/messages`` to deliver a plain text message.

        Returns ``True`` on success. Raises :class:`MaxTransportError`
        on API / network failure (the polling loop handles this).
        """
        if not text:
            raise ValueError("send_message text must be non-empty")
        body = {"chat_id": int(chat_id), "text": text}
        self._call("POST", "/messages", json_body=body)
        return True

    # ─── Internal HTTP plumbing ───────────────────────────────────

    def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> Any:
        """Execute a single HTTP request and return the parsed JSON.

        Centralises auth header injection, rate-limit handling and
        error mapping. All public methods delegate here.
        """
        url = f"{self._api_url}{path}"
        headers = {"Authorization": f"Bearer {self._bot_token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"

        # ``Session.request`` can raise a wide range of network errors
        # (ConnectionError, Timeout, OSError for DNS / socket, etc.).
        # We catch the concrete base classes plus a final ``Exception``
        # safety net so a brand-new ``requests`` release can't crash
        # the polling loop on an unknown subclass.
        try:
            response = self._session.request(
                method,
                url,
                params=params,
                json=json_body,
                headers=headers,
                timeout=30,
            )
        except (
            ConnectionError,
            TimeoutError,
            OSError,
            ValueError,
        ) as exc:
            raise MaxTransportError(
                f"MAX API request failed: {exc}",
            ) from exc
        except Exception as exc:  # noqa: BLE001 -- last-resort safety net
            logger.exception(
                "MAX API request raised unexpected %s: %s",
                type(exc).__name__,
                exc,
            )
            raise MaxTransportError(
                f"MAX API request failed: {exc}",
            ) from exc

        # Rate limiting: honour Retry-After up to a sane cap.
        if response.status_code == 429:
            retry_after = _parse_retry_after(response)
            if retry_after is not None:
                wait = min(float(retry_after), _MAX_RETRY_AFTER)
                logger.warning(
                    "MAX API rate-limited; sleeping %.1fs before retry",
                    wait,
                )
                self._sleep(wait)

        # 2xx -> parse JSON
        if 200 <= response.status_code < 300:
            if not response.content:
                return None
            try:
                return response.json()
            except ValueError:
                # Non-JSON 2xx (rare for MAX but possible). Return
                # the raw text so the caller can decide.
                return response.text

        # Non-2xx -> raise with the body for diagnostics.
        try:
            body: Any = response.json()
        except ValueError:
            body = response.text
        raise MaxTransportError(
            f"MAX API error {response.status_code} for {method} {path}",
            status_code=response.status_code,
            response_body=body,
        )


def _parse_retry_after(response: Any) -> float | None:
    """Extract a numeric ``Retry-After`` value (seconds) from a response.

    Returns ``None`` if the header is missing or unparseable.
    """
    raw = response.headers.get("Retry-After") or response.headers.get(
        "retry-after"
    )
    if not raw:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None
