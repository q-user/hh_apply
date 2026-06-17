"""Legacy compatibility shim package (issue #158).

Contains the pre-VSA storage layer (BaseModel / BaseRepository dataclass
plus 14 models and 10 repository classes) so the VSA ``StorageFacade``
and any existing call sites can keep importing the same classes.
"""
