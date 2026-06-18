"""VSA composition root (issue #155).

The new :class:`AppContainer` is a slim, pure-VSA composition root.
It exposes:

* 7 :func:`@cached_property` slice accessors (one per VSA slice);
* 1 :meth:`run` CLI entry point that builds the VSA-native
  ``BUILTIN_OPERATIONS`` parser and dispatches to the right op;
* 2 use-case factory methods (``apply_to_vacancies_use_case`` and
  ``prepare_vacancies_use_case``).

The 4 legacy ``_Adapter`` shim classes
(``_VacancySearchAdapter`` / ``_ApplicationPrepAdapter`` /
``_ApplicationSubmitAdapter`` / ``_ConfigAdapter``) are gone. The
legacy use cases are wired against the VSA slices directly via
constructor injection (issue #145 + #147 + #151).

The container is duck-typed against the legacy ``HHApplicantTool``
(no type imports of legacy classes) and never imports from
``hh_applicant_tool`` at module level — use-case construction uses
local imports inside the factory methods.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from functools import cached_property
from typing import Any, cast

logger = logging.getLogger(__name__)


class AppContainer:
    """VSA composition root.

    Holds a reference to the legacy ``HHApplicantTool`` (or any
    duck-typed stand-in exposing ``db`` / ``session`` / ``config`` /
    ``api_client`` / ``xsrf_token`` / ``storage`` / ``db_path`` /
    ``config_path`` / ``get_*_ai``) and exposes 7 VSA slice accessors
    plus 2 use-case factories and a CLI ``run`` entry point.

    Args:
        tool: the legacy :class:`HHApplicantTool`-shaped object that
            owns the live HTTP session, SQLite connection, and the
            loaded JSON config. The container accesses its
            attributes via duck typing — no explicit type imports.
    """

    def __init__(self, tool: Any) -> None:
        self._tool = tool

    # ─── 7 VSA slice accessors ─────────────────────────────────

    @cached_property
    def vacancy_search(self) -> Any:
        """Lazily build the :class:`VacancySearchSlice`."""
        from job_bot.shared.config.settings import Settings
        from job_bot.shared.storage.database import create_database
        from job_bot.vacancy_search.slice import create_vacancy_search_slice

        tool = self._tool
        config = tool.config
        settings = Settings()
        settings.database.path = tool.db_path
        hh_config = config.get("hh_api", {})
        settings.hh_api.base_url = hh_config.get(
            "base_url", "https://api.hh.ru"
        )
        settings.hh_api.user_agent = hh_config.get(
            "user_agent", "job_bot/0.1.0"
        )
        settings.hh_api.timeout = hh_config.get("timeout", 30)
        settings.hh_api.client_id = config.get("client_id")
        settings.hh_api.client_secret = config.get("client_secret")
        return create_vacancy_search_slice(
            settings=settings,
            database=create_database(settings.database.path),
        )

    @cached_property
    def application_prep(self) -> Any:
        """Lazily build the :class:`ApplicationPrepSlice`."""
        from job_bot.application_prep.slice import create_application_prep_slice
        from job_bot.shared.api.client import HHApiClient, HHApiConfig
        from job_bot.shared.config.settings import Settings
        from job_bot.shared.storage.database import create_database

        tool = self._tool
        config = tool.config
        settings = Settings()
        settings.database.path = tool.db_path
        hh_config = config.get("hh_api", {})
        settings.hh_api.base_url = hh_config.get(
            "base_url", "https://api.hh.ru"
        )
        settings.hh_api.user_agent = hh_config.get(
            "user_agent", "job_bot/0.1.0"
        )
        settings.hh_api.timeout = hh_config.get("timeout", 30)
        api_config = HHApiConfig(
            base_url=settings.hh_api.base_url,
            user_agent=settings.hh_api.user_agent,
            timeout=settings.hh_api.timeout,
        )
        api_client = HHApiClient(config=api_config)
        return create_application_prep_slice(
            settings=settings,
            database=create_database(settings.database.path),
            api_client=api_client,
            ai_client=None,
        )

    @cached_property
    def application_submit(self) -> Any:
        """Lazily build the :class:`ApplicationSubmitSlice`."""
        from job_bot.application_submit.slice import (
            create_application_submit_slice,
        )

        tool = self._tool
        return create_application_submit_slice(
            storage_conn=tool.db,
            api_client=tool.api_client,
            session=tool.session,
            xsrf_token=tool.xsrf_token,
        )

    @cached_property
    def config_auth(self) -> Any:
        """Lazily build the :class:`ConfigAuthSlice`.

        Issue #206: the slice is constructed with a
        :class:`SecretsManager` built from ``SecretsManager.from_config(...)``
        so the ``HH_PROFILE_ID`` lookup can be served by the OS
        keyring (``HH_SECRETS_BACKEND=keyring``) or any future
        :class:`SecretsBackend`. The default backend is
        :class:`EnvBackend`, which is byte-for-byte equivalent to
        the pre-issue-#206 ``os.environ.get`` path.
        """
        from job_bot.config_auth.slice import create_config_auth_slice
        from job_bot.shared.config.settings import Settings
        from job_bot.shared.secrets import SecretsManager
        from job_bot.shared.storage.database import create_database

        tool = self._tool
        settings = Settings()
        settings.database.path = tool.db_path
        settings.hh_api.base_url = "https://api.hh.ru"
        settings.hh_api.user_agent = "job_bot/0.1.0"
        settings.hh_api.timeout = 30
        # Local import of the constant keeps the module-level imports
        # clean of legacy code.
        from job_bot.shared.config.paths import CONFIG_FILENAME  # noqa: PLC0415

        config_path = tool.config_path / CONFIG_FILENAME
        # ``from_config`` consults the ``HH_SECRETS_BACKEND`` env var
        # first, then ``tool.config["secrets"]["backend"]``, then
        # falls back to :class:`EnvBackend`. The dict-based lookup
        # keeps the wiring zero-side-effect when the user has not
        # opted in to a non-default backend.
        secrets_manager = SecretsManager.from_config(tool.config or {})
        return create_config_auth_slice(
            settings=settings,
            database=create_database(settings.database.path),
            config_path=config_path,
            secrets_manager=secrets_manager,
        )

    @cached_property
    def telegram_bot(self) -> Any:
        """Lazily build the :class:`TelegramBotSlice`.

        Raises :class:`RuntimeError` when ``telegram.bot_token`` is
        missing from the config (the polling loop would be useless
        without it).
        """
        tool = self._tool
        # Issue #206: share the same SecretsManager instance the
        # ``config_auth`` slice uses, so the operator's choice of
        # backend (``HH_SECRETS_BACKEND`` / ``config["secrets"]["backend"]``)
        # is consistent across the long-running daemons.
        from job_bot.shared.secrets import SecretsManager  # noqa: PLC0415
        from job_bot.telegram_bot.slice import (  # noqa: PLC0415
            create_telegram_bot_slice,
        )
        from job_bot.telegram_bot.telegram_transport import (  # noqa: PLC0415
            TelegramTransport,
            TelegramTransportConfig,
        )

        secrets_manager = SecretsManager.from_config(tool.config or {})
        telegram_cfg = (tool.config or {}).get("telegram") or {}
        bot_token = telegram_cfg.get("bot_token") or ""
        if not bot_token:
            raise RuntimeError(
                "telegram.bot_token is required to build TelegramBotSlice"
            )
        raw_timeout = telegram_cfg.get("poll_timeout", 30)
        try:
            poll_timeout = int(raw_timeout)
        except (ValueError, TypeError):
            poll_timeout = 30
        allowed_user_ids = tuple(
            int(uid) for uid in (telegram_cfg.get("allowed_user_ids") or [])
        )
        proxy_url = telegram_cfg.get("proxy_url")
        transport = TelegramTransport(
            config=TelegramTransportConfig(
                bot_token=bot_token,
                poll_timeout=poll_timeout,
                allowed_user_ids=allowed_user_ids,
                proxy_url=proxy_url,
            ),
            session=tool.session,
            secrets_manager=secrets_manager,
        )
        return create_telegram_bot_slice(
            database=tool.db,
            transport=transport,
            config=tool.config,
        )

    @cached_property
    def max_bot(self) -> Any:
        """Lazily build the :class:`MaxBotSlice`."""
        from job_bot.max_bot.requests_transport import (
            DEFAULT_API_URL,
            RequestsMaxTransport,
        )
        from job_bot.max_bot.slice import create_max_bot_slice

        tool = self._tool
        max_cfg = (tool.config or {}).get("max") or {}
        bot_token = max_cfg.get("bot_token") or ""
        api_url = max_cfg.get("api_url") or DEFAULT_API_URL
        transport = RequestsMaxTransport(
            session=tool.session,
            bot_token=bot_token,
            api_url=api_url,
        )
        return create_max_bot_slice(transport=transport)

    @cached_property
    def channel_monitoring(self) -> Any:
        """Lazily build the :class:`ChannelMonitorSlice`."""
        from job_bot.channel_monitoring.slice import (
            create_channel_monitor_slice,
        )

        tool = self._tool
        return create_channel_monitor_slice(conn=tool.db)

    # ─── 2 use-case factories ──────────────────────────────────

    def apply_to_vacancies_use_case(
        self,
        *,
        system_prompt: str = "",
        use_ai: bool = False,
        send_email: bool = False,
    ) -> Any:
        """Return a fully-wired :class:`ApplyToVacanciesUseCase`.

        The VSA ``application_submit_slice`` is wired in (no more
        legacy adapter). The ``vacancy_search_service_factory`` is a
        thin closure that returns the
        :class:`VacancySearchSlice.search` port — the use case calls
        ``service.search_vacancies_raw(...)`` on it (the port exposes
        the same search surface the legacy adapter shimmed).
        """
        from job_bot.application_submit.services.use_cases import (  # noqa: PLC0415
            ApplyToVacanciesUseCase,
        )

        tool = self._tool
        return ApplyToVacanciesUseCase(
            api_client=tool.api_client,
            session=tool.session,
            storage=tool.storage,
            cover_letter_ai=(
                tool.get_cover_letter_ai(system_prompt) if use_ai else None
            ),
            captcha_ai=tool.get_captcha_ai(),
            xsrf_token=tool.xsrf_token,
            vacancy_filter_ai_factory=tool.get_vacancy_filter_ai,
            smtp=tool.smtp if send_email else None,
            config=tool.config,
            # Vacancy search service factory (VSA wiring) — returns
            # the slice's :class:`VacancySearchPort` so the use case
            # delegates to the VSA search port instead of the legacy
            # ``VacancySearchService``. The closure ignores the
            # ``per_page``/``total_pages`` args (the port reads
            # ``search_params`` directly); kept for backward compat
            # with the legacy use-case contract.
            vacancy_search_service_factory=lambda per_page, total_pages: (
                self.vacancy_search.search
            ),
            # VSA wiring: pass the slice directly (no more adapter).
            application_submit_slice=self.application_submit,
        )

    def prepare_vacancies_use_case(
        self,
        *,
        system_prompt: str = "",
        use_ai: bool = False,
    ) -> Any:
        """Return a fully-wired :class:`PrepareVacanciesUseCase`.

        The VSA ``application_prep_slice`` is wired in (no more
        legacy adapter). The ``vacancy_search_service_factory`` is a
        thin closure that returns the slice's search port.
        """
        from job_bot.application_submit.services.use_cases import (  # noqa: PLC0415
            PrepareVacanciesUseCase,
        )

        tool = self._tool
        cover_letter_ai = (
            tool.get_cover_letter_ai(system_prompt) if use_ai else None
        )
        return PrepareVacanciesUseCase(
            api_client=tool.api_client,
            session=tool.session,
            storage=tool.storage,
            cover_letter_ai=cover_letter_ai,
            vacancy_filter_ai_factory=tool.get_vacancy_filter_ai,
            test_ai=cover_letter_ai,
            # Vacancy search service factory (VSA wiring) — same
            # closure as ``apply_to_vacancies_use_case``. The port
            # ignores the ``per_page``/``total_pages`` args.
            vacancy_search_service_factory=lambda per_page, total_pages: (
                self.vacancy_search.search
            ),
            # Application prep service factory (VSA wiring). The
            # container exposes the slice itself; the per-profile
            # AI client build flow lives in the slice's
            # ``AiFilterService`` (issue #54 dedupe).
            application_prep_service_factory=lambda: self.application_prep,
            # VSA wiring: pass the slice directly.
            application_prep_slice=self.application_prep,
        )

    # ─── CLI entry point ──────────────────────────────────────

    def run(self, argv: Sequence[str] | None = None) -> int | None:
        """VSA-native CLI entry point.

        Issue #155 ships the entry point surface. The actual
        per-op DI wiring (``slice_=...`` for each op) and the
        production dispatch is owned by issue #154 (``job_bot.__main__``).

        For now:

        * ``run(None)`` / ``run([])`` — no command → print help,
          return ``2`` (argparse convention).
        * ``run(["<command>", ...])`` — minimal dispatch: the parser
          surface is built from :data:`job_bot.cli.BUILTIN_OPERATIONS`
          and the matching op's class is selected; the per-op
          run() / DI wiring is left for issue #154.

        Returns:
            ``2`` for ``--help`` / no-command; ``0`` for successful
            dispatch; an op-specific exit code otherwise. ``None`` is
            also accepted as a sentinel for "nothing to do".
        """
        from job_bot.cli import (  # noqa: PLC0415
            BUILTIN_OPERATIONS,
            BaseNamespace,
        )

        parser = self._build_parser(BUILTIN_OPERATIONS)
        args = parser.parse_args(
            list(argv) if argv is not None else None,
            namespace=BaseNamespace(),
        )
        # Issue #206: translate the top-level ``--secrets-backend``
        # flag into the ``HH_SECRETS_BACKEND`` env var. The
        # :class:`SecretsManager` factory reads that env var (ahead
        # of ``config["secrets"]["backend"]``), so propagating the
        # flag via the env is the cleanest way to reach every
        # ``SecretsManager.from_config`` call site in the slice
        # accessors without threading the value through every
        # constructor.
        if getattr(args, "secrets_backend", None):
            os.environ["HH_SECRETS_BACKEND"] = args.secrets_backend
        op_cls = getattr(args, "operation_class", None)
        if op_cls is None:
            parser.print_help(file=sys.stderr)
            return 2
        # Real DI wiring is owned by issue #154. Until then, we
        # construct the op with no args (the VSA ops all accept
        # optional ``slice_=`` params) and dispatch.
        try:
            op = op_cls()
        except TypeError:
            # Op needs DI; surface a clear message rather than a
            # confusing TypeError. Issue #154 will replace this path
            # with a proper dispatch table.
            logger.error(
                "Op %s requires DI wiring; see issue #154 for the "
                "production dispatch path.",
                op_cls.__name__,
            )
            return 1
        return cast("int | None", op.run(args))

    @staticmethod
    def _build_parser(
        operations: Sequence[type],
    ) -> argparse.ArgumentParser:
        """Build the VSA-native argparse parser from the registry.

        We instantiate each op with no args (the VSA ops all accept
        optional ``slice_=`` params, so ``op_cls()`` is safe) and
        call :meth:`BaseOperation.setup_parser` on the instance.
        The legacy ``HHApplicantTool._create_parser`` uses the same
        convention (op module basename becomes the sub-command
        name; legacy ``__aliases__`` are registered as extra
        ``add_parser`` calls against the same instance).
        """
        parser = argparse.ArgumentParser(prog="job_bot")
        # Issue #206: top-level ``--secrets-backend`` flag. When set,
        # the value is exported as ``HH_SECRETS_BACKEND`` for the
        # subprocess (the rest of the code reads that env var via
        # :class:`SecretsManager.from_config`). The default of
        # ``None`` (i.e. "do not override") keeps existing CLI
        # invocations byte-for-byte identical.
        parser.add_argument(
            "--secrets-backend",
            choices=("env", "keyring", "vault"),
            default=None,
            help=(
                "Externalise secrets via the named backend. "
                "Overrides config.json / HH_SECRETS_BACKEND for this "
                "invocation. Choices: env (default, current behaviour), "
                "keyring (system keyring via the [secrets] extra), "
                "vault (HashiCorp Vault; placeholder, not yet implemented)."
            ),
        )
        sub = parser.add_subparsers(dest="command")
        for op_cls in operations:
            op = op_cls()
            # The op's module basename is the canonical sub-command
            # name; legacy ``HHApplicantTool._create_parser`` uses
            # the same convention.
            module_name = op_cls.__module__.rsplit(".", 1)[-1]
            names = (module_name,) + tuple(getattr(op_cls, "__aliases__", ()))
            for name in names:
                op_name = name.replace("_", "-")
                sub_parser = sub.add_parser(op_name, help=op_cls.__doc__)
                sub_parser.set_defaults(operation_class=op_cls)
                op.setup_parser(sub_parser)
        parser.set_defaults(operation_class=None)
        return parser

    def _build_op(self, op_cls: type) -> Any:
        """Build an op instance for direct invocation (no CLI parsing)."""
        return op_cls()


__all__ = ["AppContainer"]
