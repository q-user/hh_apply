"""StorageIOPort -- interface for persisting processed vacancies / employer data.

Implemented by :class:`job_bot.application_submit.handlers.storage_io_handler.StorageIOHandler`.
The handler wraps the legacy ``_save_vacancy_to_storage`` /
``_load_employer_profile`` helpers extracted from ``ApplyToVacanciesUseCase``
(issue #145) and promoted to a dedicated handler (issue #201).
"""

from __future__ import annotations

from typing import Any, Protocol


class StorageIOPort(Protocol):
    """Persist processed vacancies, employer profiles, and site info.

    The handler is the in-slice VSA wrapper around the legacy
    ``_save_vacancy_to_storage`` / ``_load_employer_profile`` helpers.
    Side-effects are best-effort: failures are logged, not raised, so
    a broken API or DB doesn't break the apply loop.
    """

    def save_vacancy(self, vacancy: dict[str, Any]) -> None:
        """Persist a processed vacancy + its contacts (best-effort)."""
        ...

    def load_employer_profile(
        self,
        vacancy: dict[str, Any],
        seen_employers: set[str],
        site_emails: dict[str, Any],
        command: Any,
    ) -> None:
        """Fetch ``/employers/{id}``, save, and parse site for emails.

        Mutates ``seen_employers`` and ``site_emails`` in place.
        """
        ...


__all__ = ["StorageIOPort"]
