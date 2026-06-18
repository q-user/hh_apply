"""Tests for the static ``BUILTIN_OPERATIONS`` CLI registry (issue #149).

Issue #149 replaces the ``pkgutil.iter_modules`` walker in
``HHApplicantTool._create_parser`` with the static
``BUILTIN_OPERATIONS`` tuple exported from :mod:`job_bot.cli`. This
module pins that contract:

* the registry has exactly 21 entries (15 new sub-commands from #147 +
  6 existing VSA ops that were re-typed in #147);
* ``HHApplicantTool._create_parser()`` builds 21 sub-actions from it;
* every sub-action's ``--help`` text is non-empty.

The tests are intentionally narrow: they exercise the parser surface
only (argparse), not the actual ``run()`` dispatch. The run-signature
mismatch between the legacy ``BaseOperation.run(self, tool, args)`` and
the VSA ``BaseOperation.run(self, args) -> int`` is a follow-up that
lives in issue #155 (VSA-native ``__main__`` switchover).
"""

from __future__ import annotations

import argparse

import pytest

from job_bot._legacy_compat.main_stub import HHApplicantTool
from job_bot.cli import BUILTIN_OPERATIONS


def _module_name_for_op(op_cls: type) -> str:
    """Return the module basename for an op class (e.g. ``whoami``)."""
    return op_cls.__module__.rsplit(".", 1)[-1]


def _kebab_name_for_op(op_cls: type) -> str:
    """Return the CLI sub-command name for an op class (e.g. ``apply-vacancies``)."""
    return _module_name_for_op(op_cls).replace("_", "-")


def _subparsers_action(
    parser: argparse.ArgumentParser,
) -> argparse._SubParsersAction:
    """Return the :class:`_SubParsersAction` registered on ``parser``.

    The main parser owns exactly one sub-parsers action (created by
    :meth:`ArgumentParser.add_subparsers`); we return it or fail loudly.
    """
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            return action
    raise AssertionError("parser has no sub-parsers action")


def _count_unique_subcommands(
    subparsers: argparse._SubParsersAction,
) -> int:
    """Count distinct sub-parser objects registered on ``subparsers``.

    ``_SubParsersAction.choices`` is a ``{name: sub_parser}`` map that
    also includes every alias, and each alias points to the *same*
    sub-parser object as the primary name. Deduplicating by ``id()``
    therefore gives the number of ``add_parser()`` calls — i.e. the
    unique-sub-command count.
    """
    return len({id(sp) for sp in subparsers.choices.values()})


class TestBuiltinOperationsCount:
    """The registry size is pinned to 21 (issue #149 acceptance criterion)."""

    def test_registry_has_21_entries(self) -> None:
        """``BUILTIN_OPERATIONS`` is a tuple of exactly 21 op classes."""
        assert len(BUILTIN_OPERATIONS) == 21, (
            f"expected exactly 21 BUILTIN_OPERATIONS entries, "
            f"got {len(BUILTIN_OPERATIONS)}"
        )


class TestCreateParserUsesStaticRegistry:
    """``_create_parser`` iterates ``BUILTIN_OPERATIONS`` (no iter_modules)."""

    def test_create_parser_builds_21_sub_actions(self) -> None:
        """``HHApplicantTool._create_parser()`` registers exactly 21 sub-commands."""
        parser = HHApplicantTool._create_parser()
        subparsers = _subparsers_action(parser)
        assert _count_unique_subcommands(subparsers) == 21, (
            f"expected 21 sub-commands, got {_count_unique_subcommands(subparsers)}"
        )

    def test_parser_contains_every_registry_op(self) -> None:
        """Every op class in the registry has a corresponding sub-parser."""
        parser = HHApplicantTool._create_parser()
        subparsers = _subparsers_action(parser)
        for op_cls in BUILTIN_OPERATIONS:
            kebab_name = _kebab_name_for_op(op_cls)
            assert kebab_name in subparsers.choices, (
                f"sub-command '{kebab_name}' ({op_cls.__name__}) missing "
                f"from parser; available: {sorted(subparsers.choices)}"
            )

    def test_parser_exposes_no_legacy_iter_modules_ops(self) -> None:
        """Ops present in the old operations tree but **not** in
        ``BUILTIN_OPERATIONS`` must not appear in the parser.

        This pins the static-registry contract: the parser is driven by
        the registry, not by ``pkgutil.iter_modules``.
        """
        parser = HHApplicantTool._create_parser()
        subparsers = _subparsers_action(parser)
        registry_names = {_kebab_name_for_op(op) for op in BUILTIN_OPERATIONS}
        unexpected = set(subparsers.choices) - registry_names
        # Every alias points to a sub-parser that's also keyed by its
        # primary kebab name — so the only entries not in the registry
        # are aliases, which are expected.
        for name in unexpected:
            # Confirm ``name`` is a known alias for some registry op.
            assert any(
                name in getattr(op(), "__aliases__", [])
                for op in BUILTIN_OPERATIONS
            ), f"'{name}' is neither a registry op nor a known alias"


@pytest.mark.parametrize(
    "op_cls",
    BUILTIN_OPERATIONS,
    ids=lambda c: c.__name__,
)
def test_each_subaction_help_is_non_empty(op_cls: type) -> None:
    """Every sub-action registered by the static registry exposes a non-empty ``--help`` text."""
    parser = HHApplicantTool._create_parser()
    subparsers = _subparsers_action(parser)
    kebab_name = _kebab_name_for_op(op_cls)
    sub_parser = subparsers.choices[kebab_name]
    help_text = sub_parser.format_help()
    assert help_text.strip(), f"--help for '{kebab_name}' is empty"
