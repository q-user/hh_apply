"""StorageIOHandler -- persist processed vacancies / employer profiles / sites (issue #201).

In-slice VSA wrapper for the legacy
``ApplyToVacanciesUseCase._save_vacancy_to_storage`` /
``ApplyToVacanciesUseCase._load_employer_profile`` helpers (extracted
from the use case by issue #145 and now promoted to a dedicated handler
by issue #201).

Owns all writes to the VSA :class:`StorageFacade` during the per-vacancy
apply step. Side-effects are best-effort: failures are logged, not
raised, to keep the apply loop resilient.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

logger = logging.getLogger(__package__)


class StorageIOHandler:
    """In-slice storage I/O handler (issue #201).

    Args:
        storage: the VSA :class:`StorageFacade` (15-repo facade from
            PR #161). Must expose ``vacancies``, ``vacancy_contacts``,
            ``employers``, and ``employer_sites`` repositories with a
            ``save`` method. Duck-typed — any object exposing the four
            ``save``-able repos works (legacy facade, in-memory fake,
            etc.).
        api_client: HH API client (used by :meth:`load_employer_profile`
            to fetch ``/employers/{id}``). Optional; the handler
            short-circuits when missing.
        site_parser: optional callable ``(url) -> dict`` used to parse
            the employer site for emails. When ``None``, the
            load-employer path skips the site fetch + save (the
            ``site_emails`` map is left untouched).
    """

    def __init__(
        self,
        storage: Any,
        *,
        api_client: Any = None,
        site_parser: "Callable[[str], dict[str, Any]] | None" = None,
    ) -> None:
        self._storage = storage
        self._api_client = api_client
        self._site_parser = site_parser

    # ─── Public API ────────────────────────────────────────────

    def save_vacancy(self, vacancy: dict[str, Any]) -> None:
        """Persist a processed vacancy + its contacts (best-effort).

        Mirrors the legacy ``_save_vacancy_to_storage`` helper. Both
        save calls are wrapped in their own ``try`` so a failure in
        one doesn't block the other.
        """
        try:
            self._storage.vacancies.save(vacancy)
        except Exception as ex:  # noqa: BLE001
            logger.debug("save vacancy failed: %s", ex)
        if vacancy.get("contacts"):
            try:
                self._storage.vacancy_contacts.save(vacancy)
            except Exception as ex:  # noqa: BLE001
                logger.debug("save vacancy contacts failed: %s", ex)

    def load_employer_profile(
        self,
        vacancy: dict[str, Any],
        seen_employers: set[str],
        site_emails: dict[str, Any],
        command: Any,
    ) -> None:
        """Fetch ``/employers/{id}``, save, and parse site for emails.

        Mirrors the legacy ``_load_employer_profile`` helper. The
        ``seen_employers`` set is mutated in place so subsequent
        vacancies from the same employer short-circuit. The
        ``site_emails`` map is also mutated in place — the caller
        passes it across vacancies so the email handler can pick the
        right employer email per vacancy.

        Side-effects are best-effort: every ``try``/``except`` logs and
        continues. The function never raises so a broken API or DB
        doesn't break the apply loop.
        """
        employer = vacancy.get("employer") or {}
        employer_id = employer.get("id")
        if not employer_id or employer_id in seen_employers:
            return
        if self._api_client is None:
            logger.debug("load_employer_profile: api_client missing; skipping")
            return
        try:
            employer_profile = self._api_client.get(f"/employers/{employer_id}")
        except Exception as ex:  # noqa: BLE001
            logger.warning("load employer %s failed: %s", employer_id, ex)
            return
        try:
            self._storage.employers.save(employer_profile)
        except Exception as ex:  # noqa: BLE001
            logger.debug("save employer failed: %s", ex)

        if not (
            getattr(command, "send_email", False)
            and (site_url := (employer_profile.get("site_url") or "").strip())
        ):
            return
        site_url = site_url if "://" in site_url else "https://" + site_url
        logger.debug("visit site: %s", site_url)
        if self._site_parser is None:
            return
        try:
            site_info = self._site_parser(site_url)
        except Exception as ex:  # noqa: BLE001
            logger.debug("parse site %s failed: %s", site_url, ex)
            return
        emails = site_info.get("emails") or []
        site_emails[employer_id] = emails
        if site_info:
            try:
                self._storage.employer_sites.save(
                    {
                        "site_url": site_url,
                        "employer_id": employer_id,
                        "subdomains": [],
                        **site_info,
                    }
                )
            except Exception as ex:  # noqa: BLE001
                logger.debug("save employer site failed: %s", ex)


__all__ = ["StorageIOHandler"]
