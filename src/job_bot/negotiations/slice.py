"""Parent slice for the ``negotiations`` VSA package (issue #137).

The parent exposes the lifecycle sub-slice as a property so callers
can use either::

    from job_bot.negotiations import create_negotiations_slice

    parent = create_negotiations_slice(api=api)
    parent.lifecycle.run(blacklist_discard=True)

or the sub-slice directly::

    from job_bot.negotiations.lifecycle import (
        create_negotiation_lifecycle_slice,
    )

    lifecycle = create_negotiation_lifecycle_slice(api=api)
    lifecycle.run(blacklist_discard=True)
"""

from __future__ import annotations

import logging
from typing import Any

from job_bot.negotiations.lifecycle.slice import (
    NegotiationLifecycleSlice,
    create_negotiation_lifecycle_slice,
)

logger = logging.getLogger(__name__)


class NegotiationsSlice:
    """Parent slice for the ``negotiations`` domain.

    Exposes sub-slices as properties. Sub-slices are constructed
    lazily on first access; pass a ``lifecycle_slice=`` override to
    inject a custom one (used by tests).
    """

    def __init__(
        self,
        *,
        api: Any,
        blacklisted_employers: set[str] | None = None,
        lifecycle_slice: NegotiationLifecycleSlice | None = None,
    ) -> None:
        self._api = api
        self._blacklisted = set(blacklisted_employers or ())
        self._lifecycle_override = lifecycle_slice

    @property
    def api(self) -> Any:
        """The API port passed in (read-only)."""
        return self._api

    @property
    def lifecycle(self) -> NegotiationLifecycleSlice:
        """Lazily construct (or return the injected) lifecycle sub-slice."""
        if self._lifecycle_override is None:
            self._lifecycle_override = create_negotiation_lifecycle_slice(
                api=self._api,
                blacklisted_employers=self._blacklisted,
            )
        return self._lifecycle_override


def create_negotiations_slice(
    *,
    api: Any,
    blacklisted_employers: set[str] | None = None,
) -> NegotiationsSlice:
    """Factory for :class:`NegotiationsSlice`."""
    return NegotiationsSlice(
        api=api, blacklisted_employers=blacklisted_employers
    )


__all__ = [
    "NegotiationsSlice",
    "create_negotiations_slice",
    "NegotiationLifecycleSlice",
]
