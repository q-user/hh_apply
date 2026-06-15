"""Public API for the ``employer_engagement`` VSA slice (issue #137).

This slice replaces the legacy
:mod:`hh_applicant_tool.operations.reply_employers` CLI. New code
should depend on the slice directly via
:func:`create_employer_engagement_slice` or
:class:`EmployerEngagementSlice`.
"""

from .handlers.invitation_fetcher import InvitationFetcher
from .handlers.reply_composer import ReplyComposer
from .models.invitation import Invitation, MessageRecord
from .ports.api_port import (
    AIClientPort,
    EmployerActionsPort,
    MessageSourcePort,
    NegotiationSourcePort,
)
from .slice import (
    EmployerEngagementSlice,
    create_employer_engagement_slice,
)

__all__ = [
    # Slice
    "EmployerEngagementSlice",
    "create_employer_engagement_slice",
    # Handlers
    "InvitationFetcher",
    "ReplyComposer",
    # Models
    "Invitation",
    "MessageRecord",
    # Ports
    "AIClientPort",
    "EmployerActionsPort",
    "MessageSourcePort",
    "NegotiationSourcePort",
]
