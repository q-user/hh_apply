"""Resume create handler for the resume_management slice (issue #137).

Migrated from ``hh_applicant_tool.operations.create_resume``. The
handler takes a parsed resume template (``.md`` or ``.toml``) and:

1. resolves ``_suggest`` placeholders against the HH suggest API,
2. resolves industry names against the ``/industries`` catalogue,
3. optionally prints the dry-run payload,
4. POSTs the result to ``/resumes`` and (optionally) publishes it.

External dependencies are constructor-injected:

* :class:`HhApiClientPort` — the HTTP client.
* :class:`TemplateLoaderPort` — the template parser. Defaults to
  :class:`FileSystemTemplateLoader`, which reads ``.md`` via
  ``hh_applicant_tool.utils.resume_md`` and ``.toml`` via
  ``tomllib``.
"""

from __future__ import annotations

import json
import logging
import tomllib
from pathlib import Path
from typing import Any

from hh_applicant_tool.api.errors import ApiError
from job_bot.resume_management.services.resume_renderer import parse_resume_md

from job_bot.resume_management.models.options import (
    CreateOptions,
    CreateResult,
)
from job_bot.resume_management.ports.api_client_port import HhApiClientPort
from job_bot.resume_management.ports.template_loader_port import (
    TemplateLoaderPort,
)

logger = logging.getLogger(__name__)


# ── Template loaders ──────────────────────────────────────────


class FileSystemTemplateLoader:
    """Default :class:`TemplateLoaderPort` that reads from disk."""

    def load(self, path: Path) -> dict[str, Any]:
        return _load_template(path)


class InMemoryTemplateLoader:
    """Test-only loader that returns a pre-supplied dict.

    Useful when the test cares about behaviour downstream of parsing
    and just wants to inject a payload directly. Defaults to an empty
    dict so ``TemplateLoaderPort.__init__()`` is optional.
    """

    def __init__(self, payload: dict[str, Any] | None = None) -> None:
        self._payload: dict[str, Any] = payload if payload is not None else {}

    def load(self, path: Path) -> dict[str, Any]:
        # The ``path`` argument is ignored — the test pre-loaded the
        # payload. The signature is kept to satisfy the Protocol.
        return dict(self._payload)


def _load_template(path: Path) -> dict[str, Any]:
    """Read a resume template from disk.

    ``.toml`` is parsed with the stdlib ``tomllib``; ``.md`` is parsed
    with the legacy ``parse_resume_md`` helper.
    """
    if path.suffix == ".toml":
        with path.open("rb") as f:
            return tomllib.load(f)
    return parse_resume_md(path.read_text(encoding="utf-8"))


# ── Suggest / industry resolution ─────────────────────────────


def _suggest_first(
    api_client: HhApiClientPort, endpoint: str, text: str
) -> dict[str, Any] | None:
    """Return the first item from a suggest endpoint, or ``None``."""
    try:
        items = api_client.get(endpoint, text=text).get("items", [])
    except ApiError as ex:
        logger.warning("suggest %s %r: %s", endpoint, text, ex)
        return None
    return items[0] if items else None


def _resolve_suggests(api_client: HhApiClientPort, obj: Any) -> None:
    """Recursively replace ``{_suggest, text}`` with ``{id, name}``.

    Modifies *obj* in place. Skips silently when the suggest endpoint
    has no match.
    """
    if isinstance(obj, dict):
        if "_suggest" in obj and "text" in obj:
            endpoint = obj.pop("_suggest")
            text = obj.pop("text")
            found = _suggest_first(api_client, endpoint, text)
            if found:
                obj.update({"id": found.get("id"), "name": found.get("name")})
                logger.debug("resolved %r → id=%s", text, obj.get("id"))
            else:
                logger.warning(
                    "suggest не нашёл результатов для %r (endpoint: %s)",
                    text,
                    endpoint,
                )
        else:
            for v in obj.values():
                _resolve_suggests(api_client, v)
    elif isinstance(obj, list):
        for item in obj:
            _resolve_suggests(api_client, item)


def _resolve_industries(
    api_client: HhApiClientPort, experience: list[dict[str, Any]]
) -> None:
    """Resolve industry names to IDs via ``GET /industries``.

    Skips entirely when every industry already has an ``id``. On API
    failure the function logs a warning and returns; the resume
    creation flow is allowed to continue (legacy behaviour).
    """
    needs_resolve = any(
        not ind.get("id")
        for exp in experience
        for ind in exp.get("industries", [])
    )
    if not needs_resolve:
        return

    try:
        tree = api_client.get("/industries")
    except ApiError as ex:
        logger.warning("Не удалось загрузить справочник отраслей: %s", ex)
        return

    flat: dict[str, str] = {}
    for industry in tree:
        flat[industry["name"].lower()] = industry["id"]
        for sub in industry.get("industries", []):
            flat[sub["name"].lower()] = sub["id"]

    for exp in experience:
        for ind in exp.get("industries", []):
            if ind.get("id"):
                continue
            name = ind.get("name", "")
            name_l = name.lower()
            match = flat.get(name_l) or next(
                (v for k, v in flat.items() if name_l in k or k in name_l),
                None,
            )
            if match:
                ind["id"] = match
            else:
                logger.warning("Отрасль не найдена в справочнике: %r", name)


def _drop_nulls(obj: Any) -> Any:
    """Recursively drop ``None`` values from dicts/lists (legacy helper)."""
    if isinstance(obj, dict):
        return {k: _drop_nulls(v) for k, v in obj.items() if v is not None}
    if isinstance(obj, list):
        return [_drop_nulls(v) for v in obj if v is not None]
    return obj


# ── Handler ───────────────────────────────────────────────────


class ResumeCreateHandler:
    """Create a resume on hh.ru from a ``.md`` / ``.toml`` template.

    Args:
        api_client: HH API client used to POST the resume and (when
            ``publish=True``) the publish endpoint.
        template_loader: Strategy for reading a template from disk.
            Defaults to :class:`FileSystemTemplateLoader`.
    """

    def __init__(
        self,
        api_client: HhApiClientPort,
        template_loader: TemplateLoaderPort | None = None,
    ) -> None:
        self.api_client = api_client
        self._template_loader: TemplateLoaderPort = (
            template_loader
            if template_loader is not None
            else FileSystemTemplateLoader()
        )

    def create(
        self,
        template: Path,
        dry_run: bool = False,
        publish: bool = False,
    ) -> CreateResult:
        """Run the full create flow.

        Args:
            template: Path to a ``.md`` or ``.toml`` resume template.
            dry_run: Print the resolved payload to stdout; skip POST.
            publish: After creating the resume, POST to
                ``/resumes/{id}/publish``.

        Returns:
            :class:`CreateResult` describing the outcome.
        """
        if not template.exists():
            logger.error("Файл шаблона не найден: %s", template)
            return CreateResult(
                ok=False, error=f"template not found: {template}"
            )

        try:
            data = self._template_loader.load(template)
        except (OSError, ValueError) as ex:
            logger.error("Ошибка разбора шаблона: %s", ex)
            return CreateResult(ok=False, error=f"parse error: {ex}")

        _resolve_suggests(self.api_client, data)
        if experience := data.get("experience"):
            _resolve_industries(self.api_client, experience)

        payload = _drop_nulls(data)

        if dry_run:
            print(json.dumps(payload, indent=2, ensure_ascii=False))
            return CreateResult(ok=True, dry_run_payload=payload)

        try:
            result = self.api_client.post("/resumes", payload, as_json=True)
            logger.debug("POST /resumes response: %s", result)
        except ApiError as ex:
            logger.error("Ошибка при создании резюме: %s", ex)
            return CreateResult(ok=False, error=str(ex))

        resume_id = result.get("id")

        if resume_id:
            print("✅ Резюме создано")
            print(f"   https://hh.ru/resume/{resume_id}")
        else:
            print("✅ Резюме создано")

        published = False
        if publish and resume_id:
            try:
                self.api_client.post(f"/resumes/{resume_id}/publish")
                print("✅ Резюме опубликовано")
                published = True
            except ApiError as ex:
                logger.error("Ошибка при публикации: %s", ex)

        return CreateResult(ok=True, resume_id=resume_id, published=published)

    def create_with_options(self, options: CreateOptions) -> CreateResult:
        """Convenience wrapper that accepts a :class:`CreateOptions`."""
        return self.create(
            template=options.template,
            dry_run=options.dry_run,
            publish=options.publish,
        )


__all__ = [
    "FileSystemTemplateLoader",
    "InMemoryTemplateLoader",
    "ResumeCreateHandler",
    "_load_template",
    "_resolve_industries",
    "_resolve_suggests",
]
