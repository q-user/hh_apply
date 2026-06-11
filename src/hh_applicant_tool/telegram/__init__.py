"""Legacy ``hh_applicant_tool.telegram`` package.

Issue #56: this package is being replaced by the VSA
``job_bot.telegram_bot`` slice. The deprecation warning lives in the
underlying ``.transport`` module so it fires once per process (the
re-export below pulls the warning in automatically) — the package
itself does not emit a second warning, avoiding noisy duplicates.
"""

from .transport import (
    TelegramTransport,
    TelegramTransportConfig,
    TelegramTransportError,
    Update,
)

__all__ = (
    "TelegramTransport",
    "TelegramTransportConfig",
    "TelegramTransportError",
    "Update",
)
