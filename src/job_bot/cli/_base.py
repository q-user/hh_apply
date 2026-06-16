"""Base classes for VSA-style CLI sub-commands (issue #147).

This module replaces the legacy ``hh_applicant_tool.main.BaseOperation``
and ``BaseNamespace`` classes with a VSA-native shape:

* ``BaseOperation`` is the abstract shell every CLI op subclasses. The
  new contract is:
    - ``setup_parser(self, parser: argparse.ArgumentParser) -> None`` —
      add the op-specific arguments to the sub-parser.
    - ``run(self, args) -> int`` — execute the op. Returns a Unix
      exit code (0 for success, non-zero for failure).

  No more ``run(self, tool, args)``. The slice / port / HTTP session
  that the op needs is **constructor-injected** — the op declares its
  dependencies via ``__init__`` and the new ``BUILTIN_OPERATIONS``
  registry is the composition root that wires them.

  The legacy ``BaseOperation`` / ``BaseNamespace`` still live at
  ``hh_applicant_tool.main`` for the duration of the deprecation window
  and are imported there as shims.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from typing import Any, Protocol


class BaseOperation:
    """Base class for VSA CLI operations.

    Subclasses declare their dependencies via ``__init__`` and override
    :meth:`setup_parser` to add CLI arguments. They implement
    :meth:`run` to do the actual work and return an exit code.
    """

    #: Optional short aliases the dispatcher may add (kebab-cased).
    __aliases__: Sequence[str] = ()

    def setup_parser(self, parser: argparse.ArgumentParser) -> None:
        """Add the op-specific arguments to ``parser``."""

    def run(self, args: argparse.Namespace) -> int:
        """Execute the op. Return a Unix exit code."""
        raise NotImplementedError


class BaseNamespace(argparse.Namespace):
    """Common argparse attributes shared by every CLI sub-parser.

    Mirrors the legacy ``BaseNamespace`` so the dispatcher can still
    pass-through the common flags (``--config-dir``, ``--profile-id``,
    etc.). Each op subclasses this with its own fields.
    """

    profile_id: str
    config_dir: Any
    verbosity: int
    api_delay: float
    user_agent: str
    proxy_url: str
    openai_proxy_url: str


# ─── Protocol helpers (duck typing for tests) ─────────────────────────


class SupportsRun(Protocol):
    """Anything that exposes a VSA-style ``run(args) -> int`` method."""

    def run(self, args: argparse.Namespace) -> int: ...


__all__ = [
    "BaseNamespace",
    "BaseOperation",
    "SupportsRun",
]
