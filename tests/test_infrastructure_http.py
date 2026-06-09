"""Тесты инфраструктурного HTTP-клиента и парсера сайтов."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import requests

from hh_applicant_tool.infrastructure.http import (
    RequestsHttpClient,
    RequestsSiteParser,
)


def _build_session_with_response(
    text: str = "",
    status_code: int = 200,
    headers: dict[str, str] | None = None,
) -> MagicMock:
    """Создаёт мок-сессию с response-объектом, поддерживающим context manager.

    ``session.get(url)`` возвращает объект, который в ``with`` отдаёт
    response. Это критично для ``RequestsSiteParser.parse_site``, который
    использует ``with self._session.get(url) as r:``.
    """
    response = MagicMock()
    response.text = text
    response.status_code = status_code
    response.headers = headers or {}

    # raise_for_status — вызывается, кидает HTTPError на 4xx/5xx
    def raise_for_status() -> None:
        if 400 <= status_code < 600:
            raise requests.HTTPError(f"{status_code}")

    response.raise_for_status = raise_for_status

    # Контекст-менеджер: session.get() → ctx_mgr → response
    ctx = MagicMock()
    ctx.__enter__.return_value = response
    ctx.__exit__.return_value = False

    session = MagicMock()
    session.get.return_value = ctx
    return session


# ─── RequestsSiteParser: parse_site() ────────────────────────────


def test_site_parser_extracts_title():
    """Парсер извлекает <title> из HTML."""
    html = "<html><head><title>Acme Corp</title></head></html>"
    session = _build_session_with_response(text=html)
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://acme.example.com")
    assert result["title"] == "Acme Corp"


def test_site_parser_extracts_description_meta():
    """Парсер читает <meta name='description'>."""
    html = (
        "<html><head>"
        '<meta name="description" content="Мы делаем классные штуки">'
        "</head></html>"
    )
    session = _build_session_with_response(text=html)
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://acme.example.com")
    assert result["description"] == "Мы делаем классные штуки"


def test_site_parser_extracts_generator_meta():
    """Парсер читает <meta name='generator'>."""
    html = '<html><head><meta name="generator" content="WordPress 6.4"></head></html>'
    session = _build_session_with_response(text=html)
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://acme.example.com")
    assert result["generator"] == "WordPress 6.4"


def test_site_parser_unescapes_html_entities():
    """HTML-entities в title/description декодируются."""
    html = "<html><head><title>Acme &amp; Co</title></head></html>"
    session = _build_session_with_response(text=html)
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://acme.example.com")
    assert result["title"] == "Acme & Co"


def test_site_parser_extracts_emails():
    """Парсер собирает email-адреса со страницы."""
    html = (
        "<html><body>"
        "Contact us: info@acme.example.com or jobs@acme.example.com"
        "</body></html>"
    )
    session = _build_session_with_response(text=html)
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://acme.example.com")
    emails = result["emails"]
    assert "info@acme.example.com" in emails
    assert "jobs@acme.example.com" in emails


def test_site_parser_excludes_image_assets_in_emails():
    """Имена файлов (.png, .jpg) не считаются email'ами."""
    html = (
        "<html><body>"
        '<img src="banner@2x.png">'
        "real contact: support@acme.example.com"
        "</body></html>"
    )
    session = _build_session_with_response(text=html)
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://acme.example.com")
    emails = result["emails"]
    assert "support@acme.example.com" in emails
    # image filenames не должны попасть
    for e in emails:
        assert not e.endswith((".png", ".jpg", ".jpeg"))


def test_site_parser_extracts_server_header():
    """Server-заголовок попадает в server_name."""
    session = _build_session_with_response(
        text="<html></html>",
        headers={"Server": "nginx/1.25.0"},
    )
    parser = RequestsSiteParser(session=session)
    result = parser.parse_site("https://acme.example.com")
    assert result["server_name"] == "nginx/1.25.0"


def test_site_parser_extracts_x_powered_by():
    """X-Powered-By заголовок попадает в powered_by."""
    session = _build_session_with_response(
        text="<html></html>",
        headers={"X-Powered-By": "PHP/8.2"},
    )
    parser = RequestsSiteParser(session=session)
    result = parser.parse_site("https://acme.example.com")
    assert result["powered_by"] == "PHP/8.2"


def test_site_parser_handles_request_exception():
    """При сетевой ошибке — возвращается dict с пустыми полями."""
    session = MagicMock()
    session.get.side_effect = requests.ConnectionError("DNS fail")
    parser = RequestsSiteParser(session=session)

    result = parser.parse_site("https://unreachable.example.com")
    assert result["title"] == ""
    assert result["description"] == ""
    assert result["emails"] == []
    assert result["server_name"] is None


def test_site_parser_handles_http_error_status():
    """HTTP 4xx/5xx → raise_for_status → RequestException → пустой dict."""
    session = _build_session_with_response(
        text="<html>Not Found</html>",
        status_code=404,
    )
    parser = RequestsSiteParser(session=session)
    result = parser.parse_site("https://acme.example.com/404")
    assert result["title"] == ""
    assert result["emails"] == []


def test_site_parser_uses_provided_timeout():
    """Переданный timeout попадает в session.get()."""
    session = _build_session_with_response()
    parser = RequestsSiteParser(session=session, timeout=7.5)
    parser.parse_site("https://acme.example.com")
    assert session.get.call_args.kwargs.get("timeout") == 7.5


def test_site_parser_uses_user_agent_header():
    """User-Agent пробрасывается в headers запроса."""
    session = _build_session_with_response()
    parser = RequestsSiteParser(
        session=session,
        user_agent="MyBot/1.0",
    )
    parser.parse_site("https://acme.example.com")
    headers = session.get.call_args.kwargs.get("headers", {})
    assert headers.get("User-Agent") == "MyBot/1.0"


def test_site_parser_no_user_agent_means_empty_headers():
    """Без user_agent — headers пустой/не передан."""
    session = _build_session_with_response()
    parser = RequestsSiteParser(session=session)
    parser.parse_site("https://acme.example.com")
    headers = session.get.call_args.kwargs.get("headers", {})
    # Либо нет ключа User-Agent, либо он не задан
    assert "User-Agent" not in headers


def test_site_parser_extracts_ip_address():
    """Парсер достаёт IP из response.raw._connection.sock.getpeername()."""
    session = _build_session_with_response(text="<html></html>")
    # Контекст-менеджер отдаёт response, у которого есть .raw._connection
    fake_sock = MagicMock()
    fake_sock.getpeername.return_value = ("203.0.113.42", 443)
    fake_conn = MagicMock()
    fake_conn.sock = fake_sock
    # session.get.return_value.__enter__().raw._connection
    session.get.return_value.__enter__.return_value.raw._connection = fake_conn

    parser = RequestsSiteParser(session=session)
    result = parser.parse_site("https://acme.example.com")
    assert result["ip_address"] == "203.0.113.42"


def test_site_parser_ip_address_missing_is_none():
    """Если sock недоступен — ip_address остаётся None."""
    session = _build_session_with_response(text="<html></html>")
    # response.raw._connection = None
    session.get.return_value.__enter__.return_value.raw = MagicMock(
        _connection=None
    )
    parser = RequestsSiteParser(session=session)
    result = parser.parse_site("https://acme.example.com")
    assert result["ip_address"] is None


# ─── RequestsHttpClient: get / post / session ──────────────────


def test_http_client_uses_provided_session():
    """Если передана session — клиент использует её."""
    custom_session = MagicMock()
    client = RequestsHttpClient(session=custom_session)
    assert client.session is custom_session


def test_http_client_creates_default_session():
    """Без session — создаётся requests.Session() с retry-логикой."""
    with patch("requests.Session") as session_cls:
        session_cls.return_value = MagicMock()
        RequestsHttpClient()
        session_cls.assert_called_once()


def test_http_client_get_returns_response():
    """get() возвращает результат session.get()."""
    response = MagicMock()
    session = MagicMock()
    session.get.return_value = response
    client = RequestsHttpClient(session=session)

    out = client.get("https://example.com")
    assert out is response
    session.get.assert_called_once()
    # URL пробрасывается
    assert session.get.call_args[0][0] == "https://example.com"


def test_http_client_get_uses_default_timeout():
    """get() выставляет timeout= из конфига, если не передан явно."""
    session = MagicMock()
    client = RequestsHttpClient(session=session, timeout=12.0)
    client.get("https://example.com")
    assert session.get.call_args.kwargs.get("timeout") == 12.0


def test_http_client_get_respects_explicit_timeout():
    """Если timeout передан явно в get() — он используется."""
    session = MagicMock()
    client = RequestsHttpClient(session=session, timeout=12.0)
    client.get("https://example.com", timeout=30.0)
    assert session.get.call_args.kwargs.get("timeout") == 30.0


def test_http_client_post_returns_response():
    """post() возвращает результат session.post()."""
    response = MagicMock()
    session = MagicMock()
    session.post.return_value = response
    client = RequestsHttpClient(session=session)

    out = client.post("https://example.com/api", data={"a": 1})
    assert out is response
    session.post.assert_called_once()
    # URL и data пробрасываются (в session.post(url, data, **kwargs))
    call_args = session.post.call_args
    assert call_args[0][0] == "https://example.com/api"
    assert call_args.kwargs.get("data") == {"a": 1}


def test_http_client_post_uses_default_timeout():
    """post() выставляет timeout по умолчанию."""
    session = MagicMock()
    client = RequestsHttpClient(session=session, timeout=8.0)
    client.post("https://example.com/api", data={})
    assert session.post.call_args.kwargs.get("timeout") == 8.0


def test_http_client_configures_retries_on_default_session():
    """При создании session по умолчанию — retry adapter монтируется."""
    fake_session = MagicMock()
    with patch("requests.Session", return_value=fake_session):
        RequestsHttpClient(
            max_retries=5,
            backoff_factor=0.7,
            status_forcelist=(429, 500, 503),
        )
        # Конструктор уже вызван; проверяем, что adapter смонтирован
        assert fake_session.mount.called
        # Был вызов с http:// и https://
        mount_calls = fake_session.mount.call_args_list
        prefixes = {call[0][0] for call in mount_calls}
        assert "http://" in prefixes
        assert "https://" in prefixes


def test_http_client_skips_retry_setup_when_session_provided():
    """При переданной session — retry НЕ перенастраивается."""
    custom_session = MagicMock()
    RequestsHttpClient(session=custom_session)
    custom_session.mount.assert_not_called()


def test_http_client_session_property_exposes_session():
    """Свойство session даёт доступ к внутренней сессии."""
    session = MagicMock()
    client = RequestsHttpClient(session=session)
    assert client.session is session
