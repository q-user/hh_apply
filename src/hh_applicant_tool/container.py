"""Backward-compat shim for the legacy ``AppContainer`` import path (issue #155).

Issue #155 moved :class:`AppContainer` to :mod:`job_bot.container` as a
slim, pure-VSA composition root. The 4 legacy ``_Adapter`` shim classes
and the 1151-LOC DI wiring live there now.

This module is a 5-LOC stub that re-exports the new class so existing
``from hh_applicant_tool.container import AppContainer`` imports keep
working until issue #158 deletes the entire ``hh_applicant_tool``
package.
"""

from job_bot.container import AppContainer

__all__ = ["AppContainer"]
