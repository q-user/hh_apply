"""Tests for the VSA-native CLI entry point at ``job_bot.cli.main`` (issue #154).

Issue #154 moves the ``[project.scripts] hh-applicant-tool`` entry point
from the legacy CLI entry point to the VSA-native
``job_bot.cli.main:main``. The new module is a thin wrapper that:

* constructs the CLI runner (``HHApplicantTool`` until #155 lands);
* forwards ``argv`` to its ``run()`` method;
* returns the runner's exit code (or ``None``).

The contract is intentionally narrow: the entry point must behave
identically to the legacy one for end users. The body of
``HHApplicantTool.run()`` is unchanged — the wrapper just re-points
the script.

These tests pin the wrapper contract:

* ``job_bot.cli.main.main`` is callable and has the same ``argv``
  signature as the legacy entry point;
* it returns ``None`` or ``int`` (mirroring the legacy return type);
* invoking it with ``["--help"]`` triggers a clean ``SystemExit(0)``;
* ``HHApplicantTool().run`` is the actual implementation that gets
  invoked (no parallel dispatch logic in the wrapper).
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any
from unittest.mock import patch

import pytest


class TestEntryPointCallable:
    """The new VSA entry point is importable and callable."""

    def test_module_imports(self) -> None:
        """``job_bot.cli.main`` is importable."""
        from job_bot.cli import main as cli_main_module  # noqa: F401

        assert cli_main_module is not None

    def test_main_callable(self) -> None:
        """``job_bot.cli.main.main`` exists and is callable."""
        from job_bot.cli.main import main

        assert callable(main)


class TestMainSignature:
    """The new ``main`` keeps the legacy ``argv`` parameter contract."""

    def test_main_accepts_optional_argv(self) -> None:
        """``main()`` and ``main([...])`` are both valid call shapes."""
        from job_bot.cli.main import main

        # ``inspect.signature`` would also work; we use a direct call
        # with a stub to make sure the wrapper actually uses ``argv``.
        with patch("job_bot.cli.main.HHApplicantTool") as tool_cls:
            tool_cls.return_value.run.return_value = 0
            assert main() in (None, 0)
            assert main(["--help"]) in (None, 0)

    def test_main_forwards_argv_to_run(self) -> None:
        """The ``argv`` argument is forwarded to ``HHApplicantTool().run``."""
        from job_bot.cli.main import main

        with patch("job_bot.cli.main.HHApplicantTool") as tool_cls:
            tool_cls.return_value.run.return_value = 0
            main(["--help"])

        tool_cls.assert_called_once_with()
        tool_cls.return_value.run.assert_called_once_with(["--help"])

    def test_main_returns_runner_result(self) -> None:
        """``main`` returns whatever ``HHApplicantTool().run`` returned."""
        from job_bot.cli.main import main

        with patch("job_bot.cli.main.HHApplicantTool") as tool_cls:
            tool_cls.return_value.run.return_value = 42
            assert main([]) == 42


class TestMainDelegation:
    """The wrapper delegates to ``HHApplicantTool`` — no parallel logic."""

    def test_wrapper_constructs_hh_applicant_tool(self) -> None:
        """A ``HHApplicantTool`` instance is constructed per ``main()`` call."""
        from job_bot.cli.main import main

        with patch("job_bot.cli.main.HHApplicantTool") as tool_cls:
            tool_cls.return_value.run.return_value = 0
            main([])
            main([])
            assert tool_cls.call_count == 2

    def test_wrapper_does_not_catch_systemexit(self) -> None:
        """``SystemExit`` from ``--help`` (argparse) propagates out of
        ``main()`` — the wrapper must not swallow the exit code.

        This pins the "thin wrapper" contract: the wrapper does not
        add its own ``try/except``; the legacy ``HHApplicantTool.run``
        already handles all the real exceptions.
        """
        from job_bot.cli.main import main

        with patch("job_bot.cli.main.HHApplicantTool") as tool_cls:
            tool_cls.return_value.run.side_effect = SystemExit(0)
            with pytest.raises(SystemExit) as exc_info:
                main(["--help"])
            assert exc_info.value.code == 0


class TestMainHelpExit:
    """End-to-end: ``main(['--help'])`` exits with code 0."""

    def test_help_exits_with_code_0(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """The wrapper honors argparse's ``--help`` exit code."""
        from job_bot.cli.main import main

        with pytest.raises(SystemExit) as exc_info:
            main(["--help"])
        assert exc_info.value.code == 0


class TestMainArgvTypes:
    """The wrapper accepts the same ``argv`` shapes as the legacy entry point."""

    @pytest.mark.parametrize(
        "argv",
        [
            None,
            [],
            ["--help"],
            ["whoami"],
        ],
    )
    def test_argv_shapes_accepted(self, argv: Sequence[str] | None) -> None:
        """None / [] / explicit list — all valid argv inputs."""
        from job_bot.cli.main import main

        with patch("job_bot.cli.main.HHApplicantTool") as tool_cls:
            tool_cls.return_value.run.return_value = 0
            result: Any = main(argv)
            assert result in (None, 0)
            if argv is None:
                tool_cls.return_value.run.assert_called_once_with(None)
            else:
                tool_cls.return_value.run.assert_called_once_with(argv)
