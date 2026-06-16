"""VSA-native CLI entry point (issue #154).

This module is the new ``[project.scripts] hh-applicant-tool`` target
in :file:`pyproject.toml`. It replaces the legacy
``hh_applicant_tool.main:main`` entry point with a thin wrapper that:

1. Constructs :class:`hh_applicant_tool.main.HHApplicantTool` — the
   existing CLI runner that owns argparse setup, sub-command
   dispatch, and the ``try/except`` body. Issue #155 will move the
   dispatch body into a slimmed :class:`AppContainer.run`; until
   then we delegate to ``HHApplicantTool().run(argv)`` directly,
   preserving the exact behavior end users have today.
2. Forwards ``argv`` to ``run()`` unchanged.
3. Returns whatever the runner returns (``None`` or ``int`` exit
   code). The shell entry-point wrapper (``__main__.py``) calls
   ``sys.exit(main())`` to convert the value to a real exit code.

Why a thin wrapper? Two reasons:

* The script name ``hh-applicant-tool`` is preserved (CI scripts,
  Docker ``CMD``, user shell aliases all keep working) — the only
  observable change is the import path. See ``pyproject.toml`` for
  the [project.scripts] diff.
* The legacy ``HHApplicantTool.run()`` body is non-trivial (logging
  setup, ``save_token`` / ``save_cookies`` ``finally`` cleanup,
  ``_check_system`` best-effort exit, full ``try/except`` chain over
  the 6+ exception types). Re-implementing it here would duplicate
  that surface and create a parallel dispatch path. The wrapper
  just delegates.
"""

from __future__ import annotations

from collections.abc import Sequence

from hh_applicant_tool.main import HHApplicantTool


def main(argv: Sequence[str] | None = None) -> None | int:
    """VSA-native CLI entry point.

    Constructs a fresh :class:`HHApplicantTool` per call, forwards
    ``argv`` to :meth:`HHApplicantTool.run`, and returns its result.

    Args:
        argv: Optional command-line arguments. When ``None``,
            :class:`HHApplicantTool` falls back to ``sys.argv[1:]``
            (the standard library convention).

    Returns:
        The runner's exit code (``int``) or ``None`` when the runner
        completed without an explicit code. ``python -m job_bot``
        and the ``hh-applicant-tool`` script both wrap this call in
        :func:`sys.exit`.
    """
    return HHApplicantTool().run(argv)  # type: ignore[no-untyped-call]  # legacy untyped entry-point; #155 will swap to AppContainer.run()


__all__ = ["main"]
