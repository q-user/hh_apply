"""``requests``-backed :class:`MaxTransportPort` implementation.

Issue #58: a stub transport so the VSA ``MaxBotSlice`` can be wired
into the runtime (``hh_applicant_tool.operations.max_bot`` and
``AppContainer``) without depending on a specific MAX Bot API
client. The MAX Bot API specification is not yet public; once a
real client lands, it will replace this module.

The stub deliberately does **not** hit the network:

* ``get_updates`` sleeps a back-off interval and returns an empty list
  (so a long-polling loop doesn't spin).
* ``send_message`` logs the call and returns ``True`` (so the
  ``--send-message`` smoke flag doesn't error out on a placeholder).

Replace this with a real implementation when MAX Bot API is available.
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


class RequestsMaxTransport:
    """A :class:`MaxTransportPort` over ``requests.Session`` (placeholder).

    Args:
        session: a ``requests.Session`` (typically the tool's shared
            session so cookies / proxy settings are inherited).
        bot_token: the MAX Bot API token. Stored for the real client to
            pick up later.
        api_url: base URL of the MAX Bot API. Defaults to the public
            ``https://botapi.max.ru``.
    """

    def __init__(
        self,
        *,
        session: Any,
        bot_token: str,
        api_url: str = DEFAULT_API_URL,
    ) -> None:
        self._session = session
        self._bot_token = bot_token
        self._api_url = api_url.rstrip("/")

    def get_updates(
        self, offset: int | None = None, timeout: int = 30
    ) -> list[dict[str, Any]]:
        """Long-poll ``getUpdates`` — placeholder until MAX Bot API ships."""
        logger.debug(
            "MAX: get_updates(offset=%s, timeout=%s) — placeholder",
            offset,
            timeout,
        )
        time.sleep(min(_INITIAL_BACKOFF, _MAX_BACKOFF))
        return []

    def send_message(self, chat_id: int, text: str) -> bool:
        """``sendMessage`` — placeholder.

        The MAX Bot API is not yet public, so we deliberately do not
        hit the network. ``True`` keeps the ``--send-message`` smoke
        flag working until a real client replaces this stub.
        """
        logger.debug(
            "MAX: send_message(chat_id=%s, text=%r) — placeholder",
            chat_id,
            text,
        )
        return True
