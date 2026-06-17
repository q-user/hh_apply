"""Tests for the ``job_bot.cli`` BUILTIN_OPERATIONS registry (issue #147).

This is the test that drives the design of the new ``job_bot.cli`` package.
It enforces the contract that the new VSA-style CLI package is the
canonical entry point for all CLI operations, and that the registry is
immutable, well-typed, and discoverable.

The plan promises a single ``BUILTIN_OPERATIONS`` tuple that the next PR
(issue #148) will use to replace ``pkgutil.iter_modules`` in
``HHApplicantTool._create_parser``. This test pins that contract
so the dispatcher swap can be done in one place.
"""

from __future__ import annotations

import argparse

import pytest

from job_bot.cli import BUILTIN_OPERATIONS, BaseOperation
from job_bot.cli.whoami import Operation as WhoamiOperation

# The 13 new sub-commands introduced by issue #147.
NEW_OPS: tuple[str, ...] = (
    "call_api",
    "check_proxy",
    "clear_skipped",
    "config",
    "install",
    "list_resumes",
    "log",
    "logout",
    "migrate_db",
    "refresh_token",
    "settings",
    "test_session",
    "uninstall",
    "update_resumes",
    "whoami",
)

# The 6 existing VSA CLI ops that get re-typed in issue #147.
EXISTING_VSA_OPS: tuple[str, ...] = (
    "apply_vacancies",
    "apply_worker",
    "channel_monitor",
    "max_bot",
    "telegram_bot",
    "prepare_vacancies",
)

# All expected operations: 13 new + 6 existing.
ALL_EXPECTED_OPS: tuple[str, ...] = NEW_OPS + EXISTING_VSA_OPS


def _module_name_for_op(op_cls: type[BaseOperation]) -> str:
    """Return the module name (file basename) for an op class.

    Convention: every op module exposes ``Operation`` and the file name
    (without ``.py``) is the CLI sub-command name.
    """
    return op_cls.__module__.rsplit(".", 1)[-1]


class TestBuiltinOperationsRegistry:
    """The ``BUILTIN_OPERATIONS`` tuple contract."""

    def test_registry_is_a_tuple(self) -> None:
        """``BUILTIN_OPERATIONS`` is immutable: a tuple, not a list."""
        assert isinstance(BUILTIN_OPERATIONS, tuple), (
            "BUILTIN_OPERATIONS must be a tuple (immutable registry)."
        )

    def test_registry_is_non_empty(self) -> None:
        """The registry must contain at least one operation."""
        assert len(BUILTIN_OPERATIONS) > 0

    def test_registry_contains_all_expected_ops(self) -> None:
        """Every expected op is present in the registry."""
        registry_modules = {
            _module_name_for_op(op) for op in BUILTIN_OPERATIONS
        }
        missing = set(ALL_EXPECTED_OPS) - registry_modules
        assert not missing, (
            f"BUILTIN_OPERATIONS is missing ops: {sorted(missing)}"
        )

    def test_registry_has_no_duplicate_modules(self) -> None:
        """Each module name appears at most once in the registry."""
        modules = [_module_name_for_op(op) for op in BUILTIN_OPERATIONS]
        assert len(modules) == len(set(modules)), (
            f"Duplicate modules in registry: {modules}"
        )

    def test_registry_entries_are_operation_subclasses(self) -> None:
        """Every entry in the registry is a subclass of ``BaseOperation``."""
        for op in BUILTIN_OPERATIONS:
            assert isinstance(op, type), (
                f"Registry entry {op!r} is not a class."
            )
            assert issubclass(op, BaseOperation), (
                f"{op.__name__} does not subclass BaseOperation."
            )

    def test_registry_entries_have_run_method(self) -> None:
        """Every entry exposes a callable ``run`` method.

        The VSA contract is ``run(self, args) -> int`` (no more
        ``tool: HHApplicantTool`` arg). We only check that ``run`` is a
        callable instance method on the class.
        """
        for op in BUILTIN_OPERATIONS:
            assert callable(getattr(op, "run", None)), (
                f"{op.__name__}.run is missing or not callable."
            )

    def test_registry_entries_have_setup_parser_method(self) -> None:
        """Every entry exposes a ``setup_parser`` method.

        ``setup_parser`` adds the op-specific argparse arguments to the
        sub-parser. We check the method exists; we don't invoke it here.
        """
        for op in BUILTIN_OPERATIONS:
            assert callable(getattr(op, "setup_parser", None)), (
                f"{op.__name__}.setup_parser is missing or not callable."
            )

    def test_registry_contains_whoami_op(self) -> None:
        """Sanity: the first migrated op is the registry."""
        assert WhoamiOperation in BUILTIN_OPERATIONS


class TestOperationBase:
    """The VSA ``BaseOperation`` base class contract."""

    def test_operation_can_be_constructed_with_no_args(self) -> None:
        """``BaseOperation()`` is constructible with no args (default)."""
        op = BaseOperation()
        assert op is not None

    def test_setup_parser_is_overridable(self) -> None:
        """``BaseOperation.setup_parser`` accepts a parser and does nothing
        (default is a no-op). Subclasses override it."""

        class MyOp(BaseOperation):
            called = False

            def setup_parser(self, parser: argparse.ArgumentParser) -> None:
                self.called = True
                parser.add_argument("--foo")

        op = MyOp()
        parser = argparse.ArgumentParser()
        op.setup_parser(parser)
        assert op.called is True
        # The argument must be registered.
        ns = parser.parse_args(["--foo", "bar"])
        assert ns.foo == "bar"


class TestWhoamiOperationRegistration:
    """The ``whoami`` op (the first migrated op) ships in the registry."""

    def test_whoami_module_is_whoami(self) -> None:
        """The whoami class lives in ``job_bot.cli.whoami``."""
        assert _module_name_for_op(WhoamiOperation) == "whoami"

    def test_whoami_help_round_trip(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """``python -m job_bot whoami --help`` works (no crash, no traceback)."""
        parser = argparse.ArgumentParser()
        subparsers = parser.add_subparsers(dest="cmd")
        sub = subparsers.add_parser(
            "whoami", description=WhoamiOperation.__doc__
        )
        WhoamiOperation().setup_parser(sub)
        with pytest.raises(SystemExit) as exc_info:
            parser.parse_args(["whoami", "--help"])
        # --help exits with code 0.
        assert exc_info.value.code == 0
