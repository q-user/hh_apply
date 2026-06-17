"""Legacy ``HHApplicantTool`` service-locator stub (issue #158).

The pre-VSA ``HHApplicantTool`` class lived in
``src/hh_applicant_tool/main.py`` (618 LOC) and was the single
service-locator entry point for the legacy CLI/UI/test code. After
issue #158 the VSA ``AppContainer`` (in ``job_bot.container``) is the
only composition root. This stub is a minimal ``HHApplicantTool`` that
delegates field access to an ``AppContainer`` for any code path that
still imports the symbol; new code MUST use
``from job_bot.container import AppContainer`` directly.

The legacy ``HHApplicantTool`` was a megaclass with 30+ attributes
(``storage``, ``api_client``, ``config``, ``session``, ``db``,
``log_file``, ...). Tests construct it with a partial set of mocks,
so the stub must be permissive about attribute access and lazy
about the underlying container construction. We therefore:

* accept an optional ``tool`` argument and lazily wrap it in a
  minimal container view that proxies attribute reads back to the
  tool (preserves the ``tool.api_client`` / ``tool.storage`` /
  ``tool.config`` shape);
* expose a ``__getattr__`` that forwards reads to the wrapped
  ``tool`` / ``AppContainer`` and raises :class:`AttributeError`
  for truly unknown names so typos and missing mocks are caught
  loudly instead of silently turning into ``None`` (issue #177).
"""

from __future__ import annotations

import argparse
import warnings
from typing import Any

from job_bot.container import AppContainer

_DEPRECATION_MESSAGE = (
    "hh_applicant_tool.main.HHApplicantTool is deprecated; "
    "use job_bot.container.AppContainer instead (issue #158)."
)


class HHApplicantTool:
    """Backward-compat shim for the legacy service-locator (issue #158).

    New code MUST use :class:`job_bot.container.AppContainer` instead.
    The shim exists for one release window only and will be removed in
    a follow-up.
    """

    # Per-instance attributes populated by ``__new__`` and ``__init__``
    # below. Declared here so strict mypy can verify attribute access
    # on the bare class.
    _tool: Any
    _container: AppContainer | None

    def __new__(
        cls, tool: Any | None = None, *args: Any, **kwargs: Any
    ) -> "HHApplicantTool":
        warnings.warn(_DEPRECATION_MESSAGE, DeprecationWarning, stacklevel=2)
        instance = super().__new__(cls)
        instance._tool = tool
        return instance

    def __init__(
        self, tool: Any | None = None, *args: Any, **kwargs: Any
    ) -> None:
        # ``__new__`` already emitted the warning; ``__init__`` is a
        # no-op so the warning fires exactly once per instantiation.
        self._container: AppContainer | None = (
            AppContainer(tool) if tool is not None else None
        )

    @property
    def config_path(self) -> Any:
        """Legacy ``HHApplicantTool.config_path`` -- computed on demand.

        The pre-VSA class exposed a :func:`cached_property` that joined
        ``config_dir / profile_id``.  We reproduce the same shape so
        :class:`job_bot.container.AppContainer` and other call sites
        that read ``tool.config_path`` keep working unchanged.
        """
        # Resolve through the underlying tool (tests inject mocks) or
        # the container's ``config_dir`` / ``profile_id`` attributes.
        for source in (self.__dict__.get("_tool"), self):
            if source is None:
                continue
            config_dir = getattr(source, "config_dir", None)
            profile_id = getattr(source, "profile_id", None)
            if config_dir is not None and profile_id is not None:
                from pathlib import Path

                return Path(config_dir) / profile_id
        return None

    def __getattr__(self, name: str) -> Any:
        # Prefer the legacy ``tool`` (tests inject ``Mock(spec=...)``);
        # fall back to the AppContainer's properties. Unknown names
        # raise AttributeError so typos and missing mocks are caught
        # loudly (issue #177) — silently returning ``None`` masked
        # real configuration mistakes.
        tool = self.__dict__.get("_tool")
        if tool is not None and hasattr(tool, name):
            return getattr(tool, name)
        container = self.__dict__.get("_container")
        if container is not None and hasattr(container, name):
            return getattr(container, name)
        # Compute ``db`` from ``db_path`` when neither tool nor
        # container supplies it (so the VSA slices' ``tool.db``
        # reads still work end-to-end). ``getattr`` is used (not
        # ``vars(...).get``) so class-level attributes and
        # ``cached_property`` accessors are picked up too.
        if name == "db":
            import sqlite3

            for source in (tool, self):
                if source is None:
                    continue
                db_path = getattr(source, "db_path", None)
                if db_path is not None:
                    return sqlite3.connect(
                        str(db_path), check_same_thread=False
                    )
            raise AttributeError(
                "HHApplicantTool: cannot resolve 'db' — neither the "
                "wrapped tool nor the AppContainer exposes a "
                "'db_path' attribute. Set tool.db_path (or tool.db) "
                "explicitly."
            )
        raise AttributeError(f"HHApplicantTool has no attribute {name!r}")

    def run(self, argv: Any = None) -> int | None:
        """Legacy ``HHApplicantTool.run()`` entry point.

        The pre-VSA implementation owned argparse setup, sub-command
        dispatch, and a non-trivial ``try/except`` body. After
        issue #158 the body lives in :meth:`job_bot.container.AppContainer.run`
        (issue #155) — this stub just delegates so the
        ``[project.scripts] hh-applicant-tool`` entry point keeps
        working unchanged.

        Handles ``--help`` directly (argparse's default behaviour is
        to call ``sys.exit(0)``) so callers that pre-check ``--help``
        see a clean exit. The full parser is built via
        :meth:`_create_parser` (legacy compatibility shape).
        """
        # ``--help`` short-circuits to argparse's default
        # ``parser.parse_args([\"--help\"])`` behaviour (which calls
        # ``sys.exit(0)``).  We don't replicate the legacy
        # ``try/except`` chain here; the VSA :class:`AppContainer` runs
        # the op dispatch in :meth:`run` and returns the exit code.
        from job_bot.container import AppContainer

        container = self._container
        if container is None and self._tool is not None:
            container = AppContainer(self._tool)
            self._container = container
        if container is None:
            # No tool / no container — fall through to the empty
            # parser so argparse prints usage and exits.
            parser = self._create_parser()
            parser.parse_args(["--help"])
            return 0
        return container.run(argv)

    @classmethod
    def _create_parser(cls) -> argparse.ArgumentParser:
        """Legacy CLI parser builder — delegates to the VSA :class:`AppContainer`.

        The pre-VSA ``HHApplicantTool._create_parser`` walked
        ``hh_applicant_tool.operations`` with ``pkgutil.iter_modules``;
        the VSA replacement (issue #149) drives the parser from the
        static ``BUILTIN_OPERATIONS`` registry. We delegate to the
        VSA parser so legacy call sites keep working unchanged.

        Note: we re-use the legacy ``add_parser(name, aliases=[...])``
        shape so each ``BUILTIN_OPERATIONS`` entry produces a single
        sub-parser object (the aliases point at the same instance).
        The VSA :meth:`AppContainer._build_parser` uses
        ``add_parser`` per name (one per alias), which inflates the
        sub-parser count by 13; the legacy test
        ``test_create_parser_builds_21_sub_actions`` counts *unique*
        sub-parser objects, so the legacy single-parser-per-op shape
        is required.
        """
        # Imported lazily to avoid a circular import through the
        # ``job_bot.cli`` package, which itself imports the registry
        # classes that the parser iterates over.
        from job_bot.cli import BUILTIN_OPERATIONS

        parser = argparse.ArgumentParser(prog="hh-applicant-tool")
        sub = parser.add_subparsers(dest="command")
        for op_cls in BUILTIN_OPERATIONS:
            op = op_cls()
            module_name = op_cls.__module__.rsplit(".", 1)[-1]
            op_name = module_name.replace("_", "-")
            op_parser = sub.add_parser(
                op_name,
                aliases=list(getattr(op_cls, "__aliases__", ())),
                help=op_cls.__doc__,
            )
            op_parser.set_defaults(operation_class=op_cls)
            op.setup_parser(op_parser)
        parser.set_defaults(operation_class=None)
        return parser


__all__ = ["HHApplicantTool"]
