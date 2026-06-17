"""Deprecated shim — use :mod:`job_bot.shared.utils` instead (issue #151).

The cross-cutting helpers (cookie storage, terminal/console-mode control,
config directory resolution) have all moved to :mod:`job_bot.shared.utils`.
This package is preserved as a deprecation shim that re-exports the
public API from the VSA location and emits a :class:`DeprecationWarning`
on import, so legacy call sites (``from hh_applicant_tool.utils import
…``) continue to work for the duration of the VSA migration.

The markdown resume renderer (:func:`hh_applicant_tool.utils.resume_md`
in the legacy code) has moved to
:func:`job_bot.resume_management.services.resume_renderer`; the
:class:`MegaTool` mixin was deleted because no live callers remain
(see issue #151).
"""

from __future__ import annotations

import warnings

from job_bot.shared.utils._config_path import get_config_path
from job_bot.shared.utils.cookiejar import HHOnlyCookieJar
from job_bot.shared.utils.terminal import (
    print_kitty_image,
    print_sixel_mage,
    setup_terminal,
)

warnings.warn(
    "hh_applicant_tool.utils is deprecated; "
    "use job_bot.shared.utils instead (issue #151).",
    DeprecationWarning,
    stacklevel=2,
)

__all__ = [
    "HHOnlyCookieJar",
    "get_config_path",
    "print_kitty_image",
    "print_sixel_mage",
    "setup_terminal",
]
