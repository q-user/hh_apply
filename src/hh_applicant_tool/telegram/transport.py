from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests

from hh_applicant_tool.constants import CONFIG_DIR, CONFIG_FILENAME
from hh_applicant_tool.utils.config import Config

TELEGRAM_API_BASE_URL = "https://api.telegram.org"
DEFAULT_POLL_TIMEOUT = 30
DEFAULT_CONNECT_TIMEOUT = 5
DEFAULT_READ_TIMEOUT = 60
DEFAULT_MAX_RETRIES = 5
DEFAULT_BACKOFF_BASE = 1.0
DEFAULT_BACKOFF_FACTOR = 2.0
DEFAULT_MAX_BACKOFF = 30.0

Update = dict[str, Any]


class TelegramTransportError(RuntimeError):
    """Ошибка транспортного слоя Telegram Bot API."""


@dataclass(frozen=True)
class TelegramTransportConfig:
    bot_token: str
    poll_timeout: int = DEFAULT_POLL_TIMEOUT
    allowed_user_ids: tuple[int, ...] = ()


class TelegramTransport:
    def __init__(
        self,
        *,
        session: requests.Session | None = None,
        config_path: str | Path | None = None,
        config: TelegramTransportConfig | None = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base: float = DEFAULT_BACKOFF_BASE,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        max_backoff: float = DEFAULT_MAX_BACKOFF,
        connect_timeout: int = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: int = DEFAULT_READ_TIMEOUT,
        sleep_fn: Any | None = None,
    ):
        self._session = session or requests.Session()
        self._max_retries = max_retries
        self._backoff_base = backoff_base
        self._backoff_factor = backoff_factor
        self._max_backoff = max_backoff
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._sleep = sleep_fn or time.sleep

        if config is None:
            config = self._load_config(config_path)
        self._config = config

        self._base_url = f"{TELEGRAM_API_BASE_URL}/bot{self._config.bot_token}"

    @property
    def allowed_user_ids(self) -> tuple[int, ...]:
        return self._config.allowed_user_ids

    @property
    def poll_timeout(self) -> int:
        return self._config.poll_timeout

    @classmethod
    def _default_config_path(cls) -> Path:
        profile = os.getenv("HH_PROFILE_ID", ".")
        return (CONFIG_DIR / profile / CONFIG_FILENAME).resolve()

    @classmethod
    def _load_config(
        cls,
        config_path: str | Path | None,
    ) -> TelegramTransportConfig:
        cfg = Config(
            Path(config_path) if config_path else cls._default_config_path()
        )
        telegram_cfg = cfg.get("telegram") or {}

        bot_token = telegram_cfg.get("bot_token")
        if not bot_token:
            raise TelegramTransportError(
                "telegram.bot_token is required in config.json"
            )

        poll_timeout = int(
            telegram_cfg.get("poll_timeout", DEFAULT_POLL_TIMEOUT)
        )
        allowed_raw = telegram_cfg.get("allowed_user_ids") or []
        allowed_user_ids = tuple(int(user_id) for user_id in allowed_raw)

        return TelegramTransportConfig(
            bot_token=bot_token,
            poll_timeout=poll_timeout,
            allowed_user_ids=allowed_user_ids,
        )

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        last_error: Exception | None = None

        for attempt in range(self._max_retries + 1):
            try:
                response = self._session.request(
                    "GET",
                    f"{self._base_url}/{method}",
                    params=params,
                    timeout=(
                        self._connect_timeout,
                        self._read_timeout + self._config.poll_timeout,
                    ),
                )
            except requests.RequestException as exc:
                last_error = exc
                if attempt >= self._max_retries:
                    break
                self._sleep(self._retry_delay(attempt))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self._max_retries:
                    raise TelegramTransportError(
                        f"Telegram API unavailable: HTTP {response.status_code}"
                    )

                retry_after = response.headers.get("Retry-After")
                if retry_after and retry_after.isdigit():
                    delay = max(float(retry_after), self._retry_delay(attempt))
                else:
                    delay = self._retry_delay(attempt)
                self._sleep(delay)
                continue

            if response.status_code >= 400:
                raise TelegramTransportError(
                    f"Telegram API error: HTTP {response.status_code}: {response.text}"
                )

            try:
                payload = response.json()
            except ValueError as exc:
                raise TelegramTransportError(
                    "Telegram API returned invalid JSON"
                ) from exc

            if not payload.get("ok"):
                description = payload.get(
                    "description", "Unknown Telegram API error"
                )
                raise TelegramTransportError(str(description))

            return payload.get("result")

        raise TelegramTransportError(
            f"Telegram request failed after retries: {last_error}"
        ) from last_error

    def _retry_delay(self, attempt: int) -> float:
        delay = self._backoff_base * (self._backoff_factor**attempt)
        return min(delay, self._max_backoff)

    def get_updates(self, offset: int | None = None) -> list[Update]:
        params: dict[str, Any] = {"timeout": self._config.poll_timeout}
        if offset is not None:
            params["offset"] = offset

        result = self._request("getUpdates", params=params)
        if not isinstance(result, list):
            raise TelegramTransportError(
                "Telegram getUpdates result must be list"
            )

        return result

    def send_message(self, chat_id: int, text: str) -> dict[str, Any]:
        result = self._request(
            "sendMessage",
            params={"chat_id": chat_id, "text": text},
        )
        if not isinstance(result, dict):
            raise TelegramTransportError(
                "Telegram sendMessage result must be object"
            )

        return result
