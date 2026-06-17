"""HH.ru API client port for the resume_management slice (issue #137).

The slice is intentionally decoupled from the concrete ``HHApiClient``
implementation in ``job_bot.shared.api.client``. It speaks to the
:class:`HhApiClientPort` Protocol so the tests can pass an in-memory
fake without monkey-patching the shared client.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class HhApiClientPort(Protocol):
    """Subset of the HH.ru API the resume slice relies on."""

    def get(self, endpoint: str, **params: Any) -> Any:
        """Perform a ``GET`` request and return the JSON body.

        HH API endpoints return different shapes — some are dicts
        (e.g. ``/resumes``), some are lists (e.g. ``/industries``).
        Use :class:`Any` and let the caller narrow as needed.
        """
        ...

    def post(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        as_json: bool = False,
    ) -> Any:
        """Perform a ``POST`` request and return the JSON body as a dict.

        ``as_json`` mirrors the legacy flag from
        ``hh_applicant_tool.api.client.ApiClient`` — when ``True`` the
        client should serialise ``payload`` as JSON, not form-data.
        """
        ...


__all__ = ["HhApiClientPort"]
