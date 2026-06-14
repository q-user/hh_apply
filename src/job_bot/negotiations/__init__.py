"""Public API for the ``negotiations`` VSA parent package (issue #137).

This package hosts the *negotiations* domain. The first sub-slice is
``negotiations.lifecycle`` (clearing declined/old negotiations).
Future sub-slices (e.g. ``negotiations.engagement``) will live next
to it.
"""

from .slice import NegotiationsSlice, create_negotiations_slice

__all__ = [
    "NegotiationsSlice",
    "create_negotiations_slice",
]
