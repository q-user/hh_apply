"""Browser-driven OAuth login operation (VSA: ``job_bot.config_auth``).

Originally lived in ``hh_applicant_tool.operations.authorize`` (issue #59).
The module was extracted to the ``config_auth`` slice during the final
VSA switchover. The legacy module is kept as a deprecation shim that
re-exports :class:`Operation` from here.

The OAuth login flow drives a Chromium browser (via Playwright) through
hh.ru's login page and exchanges the resulting authorisation code for
an OAuth access/refresh token pair. The freshly obtained credentials
are stored via :class:`AuthHandler` (the slice's auth port), so the
CLI and the use case share the same persistence path as issue #59's
``tool.config.save_token`` contract.

The Playwright import is **deferred** (inside :meth:`Operation._run`)
so the VSA module — and the legacy ``operations/authorize`` shim that
re-exports it — can be imported on machines where Playwright is not
installed (e.g. unit-test environments, serverless workers, headless
CI). The CLI parser iterates every ``operations/`` module to build
its sub-parser list, so a top-level Playwright import would break
``HHApplicantTool()`` construction for any consumer of the CLI, not
just the ``authorize`` sub-command.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import typing
from datetime import datetime
from http.cookiejar import Cookie
from typing import TYPE_CHECKING, Any, cast
from urllib.parse import parse_qs, urlsplit

from job_bot.config_auth.handlers.auth_handler import (
    DEFAULT_PROFILE_ID,
    AuthHandler,
)
# The kitty/sixel image helpers are still in the legacy utils
# (issue #93 moved cross-cutting utilities to ``job_bot.shared.utils``
# but the image renderers stayed behind because no VSA slice depends
# on them yet). The import is deferred to :meth:`Operation._handle_captcha`
# so importing this module does not drag in ``hh_applicant_tool.utils``
# -- otherwise ``utils/__init__.py`` loads ``config.py`` which tries to
# re-export the VSA public API, creating a partial-import cycle.

if TYPE_CHECKING:
    # Imported under :data:`TYPE_CHECKING` so static type-checkers see
    # the symbol but the runtime import is deferred via
    # :func:`_require_playwright`. This keeps the public type surface
    # of the module stable while letting the actual Playwright import
    # be lazy, so the legacy ``operations/authorize`` shim (and the
    # CLI parser that scans every ``operations/`` module) can be
    # loaded on machines where the ``[playwright]`` extra is not
    # installed.
    from playwright.async_api import (
        Browser,
        BrowserContext,
        Page,
    )

    from hh_applicant_tool.main import HHApplicantTool

logger = logging.getLogger(__name__)

# Re-export the legacy name so the ``operations/authorize`` shim (and
# any external caller that imports ``DEFAULT_PROFILE_ID`` from this
# module) keeps working unchanged.
__all__ = [
    "Operation",
    "DEFAULT_PROFILE_ID",
    "AuthHandler",
]


HH_ANDROID_SCHEME = "hhandroid"


# Type alias for the ``_args`` namespace (``argparse.Namespace``
# subclass produced by ``setup_parser``). We type it as a ``Protocol``
# via ``typing.TYPE_CHECKING`` so mypy sees the attribute names we
# use (``no_headless``, ``manual``, ``username``, ``password``,
# ``use_kitty``, ``use_sixel``) without forcing callers to subclass
# argparse's ``Namespace``.
if TYPE_CHECKING:
    from typing import Protocol

    class _AuthArgs(Protocol):
        no_headless: bool
        manual: bool
        username: str | None
        password: str | None
        use_kitty: bool
        use_sixel: bool


def _require_playwright() -> Any:
    """Import Playwright lazily; raise a helpful error if missing.

    The VSA module deliberately does not import Playwright at module
    load time so other CLI sub-commands and unit tests can construct
    :class:`HHApplicantTool` (and therefore the parser that lists all
    operations) without the optional ``[playwright]`` extra installed.
    """
    try:
        from playwright.async_api import async_playwright as _pw
    except ImportError as ex:
        raise ImportError(
            "Для авторизации требуется пакет 'playwright'. "
            "Установите зависимости одной из команд:\n"
            "  uv sync --extra playwright\n"
            "  .venv/bin/playwright install chromium\n"
            "или через саму утилиту:\n"
            "  hh-applicant-tool install"
        ) from ex
    return _pw


class Operation:
    """Авторизация через Playwright.

    Moved verbatim from ``hh_applicant_tool.operations.authorize``
    during the issue #59 switchover. The class API (``setup_parser``
    / ``run`` / ``__aliases__``) and the public attribute
    ``HH_ANDROID_SCHEME`` are preserved so the legacy module's
    re-export is a true shim with no behavioural change.
    """

    __aliases__: list[str] = ["authenticate", "auth", "login"]

    # Селекторы
    SEL_LOGIN_INPUT: str = 'input[data-qa="login-input-username"]'
    SEL_EXPAND_PASSWORD: str = (
        'button[data-qa="expand-login-by_password"]'
    )
    SEL_PASSWORD_INPUT: str = 'input[data-qa="login-input-password"]'
    SEL_CODE_CONTAINER: str = 'div[data-qa="account-login-code-input"]'
    SEL_PIN_CODE_INPUT: str = 'input[data-qa="magritte-pincode-input-field"]'
    SEL_CAPTCHA_IMAGE: str = 'img[data-qa="account-captcha-picture"]'
    SEL_CAPTCHA_INPUT: str = 'input[data-qa="account-captcha-input"]'

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # The legacy module used ``super().__init__`` which was a
        # no-op for ``object``; mirror that for back-compat.
        super().__init__(*args, **kwargs)
        self._tool: HHApplicantTool | None = None
        self._args: _AuthArgs | None = None

    @property
    def is_headless(self) -> bool:
        assert self._args is not None
        return not self._args.no_headless and self.is_automated

    @property
    def is_automated(self) -> bool:
        assert self._args is not None
        return not self._args.manual

    @property
    def selector_timeout(self) -> int | None:
        return None if self.is_headless else 5000

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("username", nargs="?", help="Email или телефон")
        parser.add_argument("--password", "-p", help="Пароль для входа")
        parser.add_argument(
            "--no-headless",
            "-n",
            action="store_true",
            help="Показать окно браузера",
        )
        parser.add_argument(
            "-m", "--manual", action="store_true", help="Ручной режим ввода"
        )
        parser.add_argument(
            "-k",
            "--use-kitty",
            "--kitty",
            action="store_true",
            help="Вывод капчи в kitty",
        )
        parser.add_argument(
            "-s",
            "--use-sixel",
            "--sixel",
            action="store_true",
            help="Вывод капчи в sixel",
        )

    def run(
        self, tool: "HHApplicantTool", args: "_AuthArgs"
    ) -> int | None:
        self._tool = tool
        self._args = args
        try:
            asyncio.run(self._run())
        except (KeyboardInterrupt, asyncio.TimeoutError):
            logger.warning("Операция прервана пользователем или по таймауту")
            return 1
        return 0

    async def _run(self) -> None:
        # Deferred Playwright import — see ``_require_playwright`` for
        # the rationale. Importing here (rather than at module top)
        # is what lets the legacy ``operations/authorize`` shim be
        # loaded by the CLI parser on machines without Playwright.
        _require_playwright()

        assert self._tool is not None
        assert self._args is not None
        args = self._args
        api_client = self._tool.api_client
        storage = self._tool.storage

        if self.is_automated:
            # ``args.username`` is ``str | None``; the ``or`` chain
            # falls back to the stored setting or interactive input
            # but mypy can't prove the result is non-None, so we
            # cast explicitly (the ``if not username`` guard below
            # enforces the invariant at runtime).
            username = cast(
                "str",
                (
                    args.username
                    or storage.settings.get_value("auth.username")
                    or (
                        await asyncio.to_thread(
                            input, "Введите email или телефон: "
                        )
                    )
                ),
            ).strip()
            if not username:
                raise RuntimeError("Empty username")
            logger.debug(f"authenticate with: {username}")

        proxies = api_client.proxies
        proxy_url = proxies.get("https")
        chromium_args: list[str] = []
        if proxy_url:
            chromium_args.append(f"--proxy-server={proxy_url}")
            logger.debug(f"Используется прокси: {proxy_url}")

        if self.is_headless:
            logger.debug("Headless режим активен")

        from playwright.async_api import async_playwright

        async with async_playwright() as pw:
            logger.debug("Запуск браузера...")
            browser: Browser = await pw.chromium.launch(
                headless=self.is_headless, args=chromium_args
            )

            try:
                android_device = pw.devices["Galaxy A55"]
                context: BrowserContext = await browser.new_context(
                    **android_device
                )
                page: Page = await context.new_page()

                code_future: asyncio.Future[str | None] = asyncio.Future()

                def handle_request(request: Any) -> None:
                    url = request.url
                    if url.startswith(f"{HH_ANDROID_SCHEME}://"):
                        logger.info(f"Перехвачен OAuth redirect: {url}")
                        if not code_future.done():
                            sp = urlsplit(url)
                            code = parse_qs(sp.query).get("code", [None])[0]
                            code_future.set_result(code)

                page.on("request", handle_request)

                logger.debug(
                    f"Переход на страницу OAuth: {api_client.oauth_client.authorize_url}"
                )
                await page.goto(
                    api_client.oauth_client.authorize_url,
                    timeout=30000,
                    wait_until="load",
                )

                if self.is_automated:
                    await page.wait_for_selector(
                        self.SEL_LOGIN_INPUT, timeout=self.selector_timeout
                    )
                    await page.fill(self.SEL_LOGIN_INPUT, username)
                    logger.debug("Логин введен")

                    password = args.password or storage.settings.get_value(
                        "auth.password"
                    )
                    if password:
                        await self._direct_login(page, password)
                    else:
                        await self._onetime_code_login(page)

                logger.debug("Ожидание OAuth-кода...")
                auth_code = cast(
                    "str | None",
                    await asyncio.wait_for(
                        code_future, timeout=[None, 60.0][self.is_automated]
                    ),
                )

                page.remove_listener("request", handle_request)

                logger.debug("Код получен, пробуем получить токен...")
                token = await asyncio.to_thread(
                    api_client.oauth_client.authenticate, auth_code
                )
                api_client.handle_access_token(token)

                print("Авторизация прошла успешно!")

                if self.is_automated:
                    storage.settings.set_value("auth.username", username)
                    if args.password:
                        storage.settings.set_value(
                            "auth.password", args.password
                        )

                # ``set_value`` expects a string; the legacy
                # ``utils.config.Config`` accepted ``datetime`` via
                # ``str()`` conversion implicitly, so we mirror that
                # here (the on-disk format is unchanged).
                storage.settings.set_value(
                    "auth.last_login", str(datetime.now())
                )
                cookies = await context.cookies()
                # ``playwright``'s ``Cookie`` is a ``TypedDict`` — at
                # runtime the values are regular ``dict``​​s, which
                # is exactly what :meth:`_set_session_cookies` has
                # always consumed. The ``cast`` bridges the typed
                # return without a public-API change.
                self._set_session_cookies(
                    cast("list[dict[str, Any]]", cookies)
                )

            finally:
                logger.debug("Закрытие браузера")
                await browser.close()

    async def _direct_login(self, page: "Page", password: str) -> None:
        logger.info("Вход по паролю...")
        await page.click(self.SEL_EXPAND_PASSWORD)
        await self._handle_captcha(page)
        await page.wait_for_selector(
            self.SEL_PASSWORD_INPUT, timeout=self.selector_timeout
        )
        await page.fill(self.SEL_PASSWORD_INPUT, password)
        await page.press(self.SEL_PASSWORD_INPUT, "Enter")
        logger.debug("Форма с паролем отправлена")

    async def _onetime_code_login(self, page: "Page") -> None:
        logger.info("Вход по одноразовому коду...")
        await page.press(self.SEL_LOGIN_INPUT, "Enter")
        await self._handle_captcha(page)
        await page.wait_for_selector(
            self.SEL_CODE_CONTAINER, timeout=self.selector_timeout
        )

        print("Код был отправлен. Проверьте почту или SMS.")
        code = (
            await asyncio.to_thread(input, "Введите полученный код: ")
        ).strip()
        if not code:
            raise RuntimeError("Код подтверждения не может быть пустым.")

        await page.fill(self.SEL_PIN_CODE_INPUT, code)
        await page.press(self.SEL_PIN_CODE_INPUT, "Enter")
        logger.debug("Форма с кодом отправлена")

    async def _handle_captcha(self, page: "Page") -> None:
        try:
            captcha_element = await page.wait_for_selector(
                self.SEL_CAPTCHA_IMAGE,
                timeout=self.selector_timeout,
                state="visible",
            )
        except Exception:  # noqa: BLE001
            # Captcha is optional; any failure (timeout, page error) is treated
            # as "no captcha present" so the rest of the login flow can proceed.
            logger.debug("Капчи нет, продолжаем.")
            return

        assert self._args is not None
        args = self._args
        if not (args.use_kitty or args.use_sixel):
            raise RuntimeError(
                "Требуется ввод капчи! Используйте --kitty или --sixel."
            )

        if captcha_element is None:
            # ``wait_for_selector`` is typed as ``ElementHandle | None``
            # even with ``state="visible"``; treat a ``None`` return the
            # same as the timeout branch above (no captcha → continue).
            return
        img_bytes = await captcha_element.screenshot()
        print("\n[!] Требуется ввод капчи.")
        # Deferred import -- see the module-level comment for the
        # partial-import cycle this avoids. The helpers live in the
        # legacy ``utils.terminal`` module (no VSA dependency yet),
        # so we re-import them here on first use.
        from hh_applicant_tool.utils.terminal import (
            print_kitty_image,
            print_sixel_mage,
        )

        if args.use_kitty:
            print_kitty_image(img_bytes)
        elif args.use_sixel:
            print_sixel_mage(img_bytes)

        captcha_text = (
            await asyncio.to_thread(input, "Введите текст с картинки: ")
        ).strip()
        await page.fill(self.SEL_CAPTCHA_INPUT, captcha_text)
        await page.press(self.SEL_CAPTCHA_INPUT, "Enter")
        logger.debug("Капча отправлена")

    def _set_session_cookies(
        self, cookies: list[dict[str, typing.Any]]
    ) -> None:
        assert self._tool is not None
        for c in cookies:
            cookie = Cookie(
                version=0,
                name=c["name"],
                value=c["value"],
                port=None,
                port_specified=False,
                domain=c["domain"],
                domain_specified=True,
                domain_initial_dot=c["domain"].startswith("."),
                path=c["path"],
                path_specified=True,
                secure=c["secure"],
                expires=int(c.get("expires") or 0),
                discard=False,
                comment=None,
                comment_url=None,
                rest={"HttpOnly": str(c.get("httpOnly", False))},
                rfc2109=False,
            )
            self._tool.session.cookies.set_cookie(cookie)
