"""Config & Auth slice — main entry point and factory.

The slice aggregates the three config_auth handlers and exposes them via
their ports:

* :attr:`ConfigAuthSlice.config` — :class:`ConfigPort`
* :attr:`ConfigAuthSlice.auth` — :class:`AuthPort`
* :attr:`ConfigAuthSlice.users` — :class:`UserPort`

The factory :func:`create_config_auth_slice` wires everything from a
:class:`Settings` instance.
"""

from __future__ import annotations

from pathlib import Path

from job_bot.config_auth.handlers.auth_handler import AuthHandler
from job_bot.config_auth.handlers.config_handler import ConfigHandler
from job_bot.config_auth.handlers.user_handler import UserHandler
from job_bot.config_auth.ports.auth_port import AuthPort
from job_bot.config_auth.ports.config_port import ConfigPort
from job_bot.config_auth.ports.user_port import UserPort
from job_bot.shared.config.settings import Settings
from job_bot.shared.storage.database import Database, create_database


def _default_config_path() -> Path:
    """Pick a sensible default path for the JSON config file."""
    return Path("config.json")


class ConfigAuthSlice:
    """Aggregates config / auth / user functionality."""

    def __init__(
        self,
        database: Database,
        config_path: Path | str | None = None,
    ) -> None:
        self._database = database
        self._config_path = (
            Path(config_path)
            if config_path is not None
            else _default_config_path()
        )

        # Handlers
        self._config_handler = ConfigHandler()
        self._auth_handler = AuthHandler(database)
        self._user_handler = UserHandler(database)

    @property
    def database(self) -> Database:
        """Return the underlying database instance."""
        return self._database

    @property
    def config_path(self) -> Path:
        """Return the path the JSON config file is read from / written to."""
        return self._config_path

    @property
    def config(self) -> ConfigPort:
        """Return the config port (load/save :class:`AppConfig`)."""
        return self._config_handler

    @property
    def auth(self) -> AuthPort:
        """Return the auth port (OAuth credentials)."""
        return self._auth_handler

    @property
    def users(self) -> UserPort:
        """Return the user port (user profile CRUD)."""
        return self._user_handler


def create_config_auth_slice(
    settings: Settings | None = None,
    database: Database | None = None,
    config_path: Path | str | None = None,
) -> ConfigAuthSlice:
    """Factory function to create a :class:`ConfigAuthSlice`.

    Args:
        settings: Application settings. If ``None``, defaults are loaded.
        database: Database instance. If ``None``, one is created from
            ``settings.database.path``.
        config_path: Path to the JSON config file. If ``None``,
            ``config.json`` in the current directory is used.

    Returns:
        A fully wired :class:`ConfigAuthSlice`.
    """
    if settings is None:
        from job_bot.shared.config.settings import load_settings

        settings = load_settings()

    if database is None:
        database = create_database(settings.database.path)

    return ConfigAuthSlice(
        database=database,
        config_path=config_path,
    )
