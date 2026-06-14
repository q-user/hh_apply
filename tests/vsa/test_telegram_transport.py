from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests

from job_bot.telegram_bot.telegram_transport import TelegramTransport


def _response(
    status_code: int,
    payload: dict,
    headers: dict[str, str] | None = None,
) -> requests.Response:
    response = requests.Response()
    response.status_code = status_code
    response.headers.update(headers or {})
    response._content = json.dumps(payload).encode("utf-8")
    response.url = "https://api.telegram.org"
    return response


def _write_config(
    path: Path,
    token: str = "test-token",
    proxy_url: str | None = None,
) -> None:
    telegram_cfg = {
        "bot_token": token,
        "poll_timeout": 15,
        "allowed_user_ids": [1, "2"],
    }
    if proxy_url is not None:
        telegram_cfg["proxy_url"] = proxy_url
    path.write_text(
        json.dumps({"telegram": telegram_cfg}),
        encoding="utf-8",
    )


def test_loads_telegram_config_from_config_json(tmp_path: Path):
    config_path = tmp_path / "config.json"
    _write_config(config_path)

    session = Mock(spec=requests.Session)
    session.request.return_value = _response(200, {"ok": True, "result": []})

    transport = TelegramTransport(session=session, config_path=config_path)

    updates = transport.get_updates()

    assert updates == []
    assert transport.poll_timeout == 15
    assert transport.allowed_user_ids == (1, 2)
    _, kwargs = session.request.call_args
    assert kwargs["params"] == {"timeout": 15}


def test_get_updates_retries_on_network_error_with_exponential_backoff(
    tmp_path: Path,
):
    config_path = tmp_path / "config.json"
    _write_config(config_path)

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        requests.ConnectionError("network"),
        _response(
            200,
            {
                "ok": True,
                "result": [{"update_id": 101, "message": {"text": "hi"}}],
            },
        ),
    ]

    sleep_calls: list[float] = []
    transport = TelegramTransport(
        session=session,
        config_path=config_path,
        sleep_fn=sleep_calls.append,
        backoff_base=0.5,
        backoff_factor=2,
    )

    updates = transport.get_updates(offset=10)

    assert updates[0]["update_id"] == 101
    assert sleep_calls == [0.5]


def test_get_updates_retries_on_429_and_5xx(tmp_path: Path):
    config_path = tmp_path / "config.json"
    _write_config(config_path)

    session = Mock(spec=requests.Session)
    session.request.side_effect = [
        _response(429, {"ok": False}, headers={"Retry-After": "3"}),
        _response(502, {"ok": False}),
        _response(200, {"ok": True, "result": []}),
    ]

    sleep_calls: list[float] = []
    transport = TelegramTransport(
        session=session,
        config_path=config_path,
        sleep_fn=sleep_calls.append,
        backoff_base=1,
        backoff_factor=2,
    )

    assert transport.get_updates() == []
    assert sleep_calls == [3.0, 2.0]


def test_send_message(tmp_path: Path):
    config_path = tmp_path / "config.json"
    _write_config(config_path)

    session = Mock(spec=requests.Session)
    session.request.return_value = _response(
        200,
        {
            "ok": True,
            "result": {"message_id": 42, "chat": {"id": 123}},
        },
    )

    transport = TelegramTransport(session=session, config_path=config_path)
    message = transport.send_message(chat_id=123, text="hello")

    assert message["message_id"] == 42
    _, kwargs = session.request.call_args
    assert kwargs["params"] == {"chat_id": 123, "text": "hello"}


def test_raises_when_bot_token_missing(tmp_path: Path):
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"telegram": {}}), encoding="utf-8")

    with pytest.raises(Exception, match="bot_token"):
        TelegramTransport(config_path=config_path)


def test_loads_proxy_url_from_config(tmp_path: Path):
    """Test that proxy_url is loaded from config and set on session."""
    config_path = tmp_path / "config.json"
    _write_config(config_path, proxy_url="socks5://user:pass@host:1080")

    session = Mock(spec=requests.Session)
    session.request.return_value = _response(200, {"ok": True, "result": []})

    transport = TelegramTransport(session=session, config_path=config_path)

    # Verify proxy is configured on session
    assert session.proxies == {
        "http": "socks5://user:pass@host:1080",
        "https": "socks5://user:pass@host:1080",
    }
    assert transport._config.proxy_url == "socks5://user:pass@host:1080"


def test_no_proxy_when_not_configured(tmp_path: Path):
    """Test that no proxy is set when proxy_url is not in config."""
    config_path = tmp_path / "config.json"
    _write_config(config_path)  # No proxy_url

    session = requests.Session()

    def mock_request(*args, **kwargs):
        return _response(200, {"ok": True, "result": []})

    session.request = mock_request

    transport = TelegramTransport(session=session, config_path=config_path)

    # Verify no proxy is configured
    assert session.proxies == {}
    assert transport._config.proxy_url is None


def test_proxy_via_telegram_transport_config():
    """Test that proxy can be passed via TelegramTransportConfig directly."""
    from job_bot.telegram_bot.telegram_transport import TelegramTransportConfig

    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=15,
        allowed_user_ids=(1, 2),
        proxy_url="socks5://localhost:1080",
    )

    session = Mock(spec=requests.Session)
    session.request.return_value = _response(200, {"ok": True, "result": []})

    transport = TelegramTransport(session=session, config=config)

    assert session.proxies == {
        "http": "socks5://localhost:1080",
        "https": "socks5://localhost:1080",
    }
    assert transport._config.proxy_url == "socks5://localhost:1080"


def test_no_proxy_via_telegram_transport_config():
    """Test that no proxy is set when proxy_url is None in TelegramTransportConfig."""
    from job_bot.telegram_bot.telegram_transport import TelegramTransportConfig

    config = TelegramTransportConfig(
        bot_token="test-token",
        poll_timeout=15,
        allowed_user_ids=(1, 2),
        proxy_url=None,
    )

    session = requests.Session()

    def mock_request(*args, **kwargs):
        return _response(200, {"ok": True, "result": []})

    session.request = mock_request

    transport = TelegramTransport(session=session, config=config)

    assert session.proxies == {}
    assert transport._config.proxy_url is None
