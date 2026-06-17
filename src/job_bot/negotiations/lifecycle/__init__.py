"""Public API for the ``negotiations.lifecycle`` VSA sub-slice (issue #137).

This sub-slice replaces the legacy
:mod:`hh_applicant_tool.operations.clear_negotiations` CLI. New code
should depend on the slice directly via
:func:`create_negotiation_lifecycle_slice` or
:class:`NegotiationLifecycleSlice`.
"""

from .models.negotiation_state import (
    NegotiationLifecycleResult,
    NegotiationRecord,
)
from .ports.api_port import LifecycleApiPort
from .slice import (
    NegotiationLifecycleSlice,
    create_negotiation_lifecycle_slice,
)

__all__ = [
    "NegotiationLifecycleSlice",
    "create_negotiation_lifecycle_slice",
    "NegotiationLifecycleResult",
    "NegotiationRecord",
    "LifecycleApiPort",
]
