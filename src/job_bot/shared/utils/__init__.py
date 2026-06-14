"""Cross-cutting utility helpers shared across VSA slices.

Modules here are intentionally dependency-free and provide small,
focused helpers (text formatting, datetime parsing, logging setup,
JSON encoding). VSA slices should import from these locations
directly. The legacy ``hh_applicant_tool.utils.*`` modules re-export
from here as deprecation shims.
"""
