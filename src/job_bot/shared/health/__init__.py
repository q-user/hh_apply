"""Shared health-endpoint utilities (issue #208).

This package exposes a tiny, dependency-free HTTP server that the long
running CLI daemons (``apply-worker``, ``telegram-bot``, ``max-bot``)
can spin up so an external supervisor (Docker, Kubernetes, systemd)
can liveness- and readiness-probe them.

Public surface:

* :class:`HealthServer` -- the HTTP server (in :mod:`.server`).
* :class:`HealthChecks` -- the Protocol the server consumes to answer
  ``/ready`` (in :mod:`.checks`).
* :class:`DefaultHealthChecks` -- the default implementation that
  pings the SQLite DB and the HH API root (in :mod:`.checks`).

Typical CLI usage::

    health = HealthServer(
        port=args.health_port,
        checks=DefaultHealthChecks(database=db, hh_api=api),
    )
    health.start()
    try:
        ...  # main daemon loop
    finally:
        health.stop()

Why a separate module?  Two reasons:

* The shared kernel (``job_bot.shared``) is the canonical home for
  cross-slice utilities; the health server has no slice-specific
  dependencies, so it does not belong in any single slice.
* The Protocol + default impl split lets each daemon op inject a
  tailored probe (e.g. ``apply-worker`` could add an "is the queue
  worker registered?" check) without touching this module.
"""

from __future__ import annotations

from job_bot.shared.health.checks import (
    DefaultHealthChecks,
    HealthChecks,
    TrivialHealthChecks,
)
from job_bot.shared.health.server import DEFAULT_HOST, HealthServer

__all__ = [
    "DEFAULT_HOST",
    "DefaultHealthChecks",
    "HealthChecks",
    "HealthServer",
    "TrivialHealthChecks",
]
