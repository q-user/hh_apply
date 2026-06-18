"""Health-check Protocols and default implementations (issue #208).

The HTTP server in :mod:`job_bot.shared.health.server` does not run
checks itself -- it delegates to whatever object satisfies
:class:`HealthChecks`. That keeps the transport layer pure (no DB /
HTTP imports) and lets each daemon op inject a tailored set of
probes (e.g. ``apply-worker`` checks the apply-jobs queue, the
telegram bot checks the long-polling transport).

The default implementation :class:`DefaultHealthChecks` covers the
two cross-cutting dependencies every long-running daemon has:

* The SQLite database is openable and responsive (``SELECT 1``).
* The HH API ``https://api.hh.ru/`` answers ``HEAD`` within the
  configured timeout.

Both probes swallow exceptions and surface them as ``(False, msg)``
so a misbehaving dep never crashes the readiness handler thread.

Why a :class:`typing.Protocol`?  Structural typing means each daemon
op can pass a plain object that satisfies the shape -- no need to
inherit from a base class or import this module just to construct
one.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Protocol, runtime_checkable

from job_bot.shared.api.client import HHApiClient
from job_bot.shared.storage.database import Database

logger = logging.getLogger(__package__)


@runtime_checkable
class HealthChecks(Protocol):
    """Protocol for objects that answer ``/health`` and ``/ready`` probes.

    Implementations must be *thread-safe*: :class:`HealthServer`
    invokes them from a background thread on every HTTP request. The
    simplest way to satisfy this is to keep them stateless.
    """

    def liveness(self) -> tuple[bool, str]:
        """Return ``(ok, message)`` for the trivial liveness check.

        Called by callers that want to expose a richer liveness
        surface than the bare ``HTTP 200`` returned by ``/health``.
        The default ``/health`` handler does *not* consult this --
        the fact that the HTTP server is accepting requests is
        itself proof the process is alive.
        """
        ...

    def readiness(self) -> tuple[bool, str]:
        """Return ``(ok, message)`` for the readiness check.

        A non-ok result is translated into ``HTTP 503`` by the
        handler. ``message`` is the reason string, surfaced in the
        response body so operators can ``curl /ready`` and read why
        the pod is NotReady without ssh-ing in.
        """
        ...


class TrivialHealthChecks:
    """A :class:`HealthChecks` that always reports ready.

    Useful for CLI ops that have no external dependencies worth
    probing (e.g. the telegram-bot long-polling loop does its own
    retry logic; the only thing that can go wrong is the transport,
    which is already covered by ``/health``'s "process is alive"
    semantics). Returning ``(True, "ready")`` keeps ``/ready`` useful
    as an "is the daemon process up?" signal even when there is
    nothing deeper to check.
    """

    def liveness(self) -> tuple[bool, str]:
        return True, "alive"

    def readiness(self) -> tuple[bool, str]:
        return True, "ready"


class DefaultHealthChecks:
    """Default :class:`HealthChecks` -- probes DB (and optionally HH API).

    Args:
        database: The :class:`Database` to ping on every readiness
            check. ``SELECT 1`` is the cheapest possible query.
        hh_api: Optional object with a ``ping()`` method that raises
            on failure. The production :class:`HHApiClient` satisfies
            this shape; tests inject a fake. When ``None`` only the
            DB is probed (useful for daemons that don't talk to
            ``api.hh.ru``, e.g. the telegram bot).

    Thread-safety: :class:`Database` opens a fresh connection per
    ``SELECT 1`` so no shared state is touched. ``HHApiClient.ping``
    is documented as thread-safe in its module docstring. The two
    probes therefore need no locking.
    """

    def __init__(
        self,
        *,
        database: Database | sqlite3.Connection,
        hh_api: HHApiClient | None = None,
    ) -> None:
        self._db = database
        self._hh_api = hh_api

    def liveness(self) -> tuple[bool, str]:
        """Liveness is always OK -- the server thread answers, so we're alive."""
        return True, "alive"

    def readiness(self) -> tuple[bool, str]:
        """Return ``(True, "ready")`` iff DB (and HH API if configured) respond.

        Failures are *caught* (logged at WARNING) and surfaced as
        ``(False, "db: <reason>")`` / ``(False, "hh: <reason>")``.
        Order is DB first, then HH API; we stop at the first failure
        so the message is unambiguous.
        """
        db_ok, db_msg = self._probe_db()
        if not db_ok:
            return False, f"db: {db_msg}"
        if self._hh_api is None:
            return True, "ready"
        hh_ok, hh_msg = self._probe_hh()
        if not hh_ok:
            return False, f"hh: {hh_msg}"
        return True, "ready"

    # ─── private probes ───────────────────────────────────────────

    def _probe_db(self) -> tuple[bool, str]:
        # Accept either a ``Database`` wrapper (we open a fresh
        # connection per probe) or a raw ``sqlite3.Connection`` (the
        # caller owns the lifetime; we just ``SELECT 1`` on it).
        try:
            if isinstance(self._db, Database):
                with self._db.connect() as conn:
                    row = conn.execute("SELECT 1").fetchone()
            else:
                row = self._db.execute("SELECT 1").fetchone()
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: db probe failed: %s", exc)
            return False, str(exc) or exc.__class__.__name__
        if row is None:
            return False, "db returned no row"
        return True, "db ok"

    def _probe_hh(self) -> tuple[bool, str]:
        try:
            self._hh_api.ping()  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            logger.warning("health: hh api probe failed: %s", exc)
            return False, str(exc) or exc.__class__.__name__
        return True, "hh ok"


__all__ = ["DefaultHealthChecks", "HealthChecks", "TrivialHealthChecks"]
