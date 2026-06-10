from __future__ import annotations

import os
from functools import lru_cache


@lru_cache(maxsize=1)
def get_android_client_id() -> str | None:
    """Get Android client ID from environment variable."""
    return os.getenv("HH_ANDROID_CLIENT_ID")


@lru_cache(maxsize=1)
def get_android_client_secret() -> str | None:
    """Get Android client secret from environment variable."""
    return os.getenv("HH_ANDROID_CLIENT_SECRET")


# Note: Users must provide their own hh.ru OAuth client credentials.
# Set HH_ANDROID_CLIENT_ID and HH_ANDROID_CLIENT_SECRET environment variables
# or provide client_id and client_secret in the config.json file.
