"""Legacy storage compatibility shim (issue #158).

The pre-VSA storage layer (BaseModel / BaseRepository dataclass plus
the 14 SQLAlchemy-style models and 10 repository classes) is preserved
here so the VSA ``StorageFacade`` and any existing call sites can keep
importing the same classes. Migration target is per-slice ``models/`` /
``repositories/`` in a follow-up cleanup; for issue #158, the goal is
to delete the ``hh_applicant_tool`` distribution package only.
"""

from __future__ import annotations

from .facade import StorageFacade

__all__ = ["StorageFacade"]
