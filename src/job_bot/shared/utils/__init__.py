"""Cross-cutting utility helpers shared across VSA slices.

Modules here are intentionally dependency-free and provide small,
focused helpers (text formatting, datetime parsing, logging setup,
JSON encoding, cookie storage, terminal / console-mode control,
config directory resolution). VSA slices should import from these
locations directly. The legacy ``hh_applicant_tool.utils.*`` modules
re-export from here as deprecation shims.
"""

from job_bot.shared.utils._config_path import get_config_path
from job_bot.shared.utils.cookiejar import HHOnlyCookieJar
from job_bot.shared.utils.terminal import (
    print_kitty_image,
    print_sixel_mage,
    setup_terminal,
)

__all__ = [
    "HHOnlyCookieJar",
    "get_config_path",
    "print_kitty_image",
    "print_sixel_mage",
    "setup_terminal",
]
