"""Tests for the resume_management VSA slice (issue #137).

Covers the resume creation (markdown/TOML template) and clone workflows
migrated from ``hh_applicant_tool.operations.create_resume`` and
``hh_applicant_tool.operations.clone_resume`` into a self-contained
vertical slice. HH API calls are replaced with an in-memory fake; the
markdown parser and TOML loader are real.
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from job_bot.resume_management.handlers.resume_clone_handler import (
    ResumeCloneHandler,
)
from job_bot.resume_management.handlers.resume_create_handler import (
    ResumeCreateHandler,
    _load_template,
    _resolve_industries,
    _resolve_suggests,
)
from job_bot.resume_management.ports.api_client_port import HhApiClientPort
from job_bot.resume_management.ports.template_loader_port import (
    TemplateLoaderPort,
)
from job_bot.resume_management.slice import (
    ResumeManagementSlice,
    create_resume_management_slice,
)

# ─── In-memory fakes ─────────────────────────────────────────


class _FakeApiClient:
    """In-memory :class:`HhApiClientPort` that records every call.

    Behaviour is configured per-test by assigning a value to one of
    the ``responses`` / ``resumes`` / ``suggest`` attributes. The
    ``captured`` list keeps a chronological record of every method
    call so tests can assert on the wire-level interaction.
    """

    def __init__(self) -> None:
        self.captured: list[tuple[str, dict[str, Any]]] = []
        self.resumes: list[dict[str, Any]] = []
        self.industries: list[dict[str, Any]] = []
        self.suggests: dict[str, list[dict[str, Any]]] = {}
        self.post_response: dict[str, Any] = {"id": "fake-new-resume"}
        self.raise_on_post: Exception | None = None

    # ── HH API surface (subset used by the handlers) ─────────

    def get(self, endpoint: str, **params: Any) -> Any:
        self.captured.append(("get", {"endpoint": endpoint, **params}))
        if endpoint == "/resumes/mine":
            return {"items": list(self.resumes)}
        if endpoint == "/industries":
            return list(self.industries)
        if "text" in params:
            text = params["text"]
            items = self.suggests.get(endpoint, [])
            return {"items": [i for i in items if i.get("name") == text]}
        return {}

    def post(
        self,
        endpoint: str,
        payload: dict[str, Any] | None = None,
        as_json: bool = False,
    ) -> Any:
        self.captured.append(
            (
                "post",
                {
                    "endpoint": endpoint,
                    "payload": payload,
                    "as_json": as_json,
                },
            )
        )
        if self.raise_on_post is not None:
            raise self.raise_on_post
        return dict(self.post_response)


@pytest.fixture
def api_client() -> _FakeApiClient:
    return _FakeApiClient()


@pytest.fixture
def fake_loader() -> TemplateLoaderPort:
    """Minimal in-process template loader backed by a dict."""
    from job_bot.resume_management.handlers.resume_create_handler import (
        InMemoryTemplateLoader,
    )

    return InMemoryTemplateLoader()


# ─── Slice / factory wiring ───────────────────────────────────


class TestResumeManagementSlice:
    def test_create_slice(self) -> None:
        slice_ = ResumeManagementSlice(
            api_client=MagicMock(spec=HhApiClientPort)
        )
        assert slice_.api_client is not None
        assert slice_.create_resume is not None
        assert slice_.clone_resume is not None

    def test_factory_returns_configured_slice(
        self, api_client: _FakeApiClient
    ) -> None:
        slice_ = create_resume_management_slice(api_client=api_client)
        assert isinstance(slice_, ResumeManagementSlice)
        assert slice_.create_resume.api_client is api_client
        assert slice_.clone_resume.api_client is api_client


# ─── Template loading (.md / .toml) ───────────────────────────


class TestTemplateLoading:
    def test_load_markdown_template(
        self, tmp_path: Path, fake_loader: TemplateLoaderPort
    ) -> None:
        md_path = tmp_path / "resume.md"
        md_path.write_text(
            "# Резюме\n\n"
            "## Личные данные\n\n"
            "- Имя: Иван\n"
            "- Фамилия: Иванов\n"
            "- Отчество: Иванович\n\n"
            "## Желаемая должность\n\n"
            "Python-разработчик\n",
            encoding="utf-8",
        )
        data = _load_template(md_path)
        assert isinstance(data, dict)
        assert data.get("first_name") == "Иван"
        assert data.get("last_name") == "Иванов"
        assert data.get("title") == "Python-разработчик"

    def test_load_toml_template(
        self, tmp_path: Path, fake_loader: TemplateLoaderPort
    ) -> None:
        toml_path = tmp_path / "resume.toml"
        toml_path.write_text(
            (
                'first_name = "Petr"\n'
                'last_name = "Petrov"\n'
                'title = "Python Developer"\n'
            ),
            encoding="utf-8",
        )
        with toml_path.open("rb") as f:
            data = tomllib.load(f)
        assert data["first_name"] == "Petr"
        assert data["last_name"] == "Petrov"

    def test_template_loader_protocol_method_exists(self) -> None:
        """The :class:`TemplateLoaderPort` Protocol must expose ``load``."""
        loader = MagicMock(spec=TemplateLoaderPort)
        loader.load.return_value = {"first_name": "X"}
        result = loader.load(Path("any.md"))
        assert result == {"first_name": "X"}


# ─── Suggest resolution (merge markdown + suggestion dict) ───


class TestSuggestResolution:
    def test_suggest_resolves_into_id_name(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.suggests = {
            "/areas": [{"id": "1", "name": "Москва"}],
        }

        data: dict[str, Any] = {
            "area": {"_suggest": "/areas", "text": "Москва"},
        }
        _resolve_suggests(api_client, data)
        assert data["area"]["id"] == "1"
        assert data["area"]["name"] == "Москва"
        assert "_suggest" not in data["area"]
        assert "text" not in data["area"]

    def test_suggest_misses_are_left_intact(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.suggests = {}  # nothing matches

        data: dict[str, Any] = {
            "area": {"_suggest": "/areas", "text": "Венесуэла"},
        }
        _resolve_suggests(api_client, data)
        # Nothing to update — the placeholder stays.
        assert data["area"].get("id") is None

    def test_suggest_recurses_into_nested(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.suggests = {
            "/areas": [{"id": "2", "name": "СПб"}],
        }

        data: dict[str, Any] = {
            "experience": [
                {
                    "company": "Acme",
                    "area": {"_suggest": "/areas", "text": "СПб"},
                }
            ]
        }
        _resolve_suggests(api_client, data)
        assert data["experience"][0]["area"]["id"] == "2"


# ─── Industry resolution ─────────────────────────────────────


class TestIndustryResolution:
    def test_resolve_industries_against_catalogue(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.industries = [
            {
                "id": "ind-1",
                "name": "IT",
                "industries": [
                    {"id": "sub-1", "name": "Software"},
                ],
            }
        ]

        experience = [{"industries": [{"name": "Software"}]}]
        _resolve_industries(api_client, experience)
        assert experience[0]["industries"][0]["id"] == "sub-1"

    def test_resolve_industries_skips_when_all_have_ids(
        self, api_client: _FakeApiClient
    ) -> None:
        experience = [{"industries": [{"id": "x", "name": "IT"}]}]
        _resolve_industries(api_client, experience)
        # No API call should have been made.
        assert all(c[0] != "get" for c in api_client.captured)


# ─── Create-resume handler ───────────────────────────────────


class TestResumeCreateHandler:
    def test_create_resume_posts_payload(
        self,
        api_client: _FakeApiClient,
        tmp_path: Path,
    ) -> None:
        api_client.post_response = {"id": "abc-123"}
        template = tmp_path / "r.toml"
        template.write_text(
            'first_name = "Petr"\nlast_name = "Petrov"\n',
            encoding="utf-8",
        )
        handler = ResumeCreateHandler(api_client=api_client)
        result = handler.create(template=template, dry_run=False, publish=False)

        # It called POST /resumes with the parsed payload.
        post_calls = [c for c in api_client.captured if c[0] == "post"]
        assert len(post_calls) == 1
        endpoint, payload_dict = (
            post_calls[0][1]["endpoint"],
            post_calls[0][1]["payload"],
        )
        assert endpoint == "/resumes"
        assert payload_dict["first_name"] == "Petr"
        assert payload_dict["last_name"] == "Petrov"
        assert result.resume_id == "abc-123"

    def test_create_resume_dry_run_skips_post(
        self,
        api_client: _FakeApiClient,
        tmp_path: Path,
    ) -> None:
        template = tmp_path / "r.toml"
        template.write_text('first_name = "Petr"\n', encoding="utf-8")
        handler = ResumeCreateHandler(api_client=api_client)
        result = handler.create(template=template, dry_run=True, publish=False)
        # No POST should have been issued.
        assert all(c[0] != "post" for c in api_client.captured)
        assert result.dry_run_payload is not None
        assert result.dry_run_payload["first_name"] == "Petr"

    def test_create_resume_publishes_when_requested(
        self,
        api_client: _FakeApiClient,
        tmp_path: Path,
    ) -> None:
        api_client.post_response = {"id": "abc-999"}
        template = tmp_path / "r.toml"
        template.write_text('first_name = "P"\n', encoding="utf-8")
        handler = ResumeCreateHandler(api_client=api_client)
        result = handler.create(template=template, dry_run=False, publish=True)
        # Two POSTs: one for /resumes, one for /resumes/{id}/publish.
        posts = [c for c in api_client.captured if c[0] == "post"]
        assert len(posts) == 2
        assert posts[1][1]["endpoint"] == "/resumes/abc-999/publish"
        assert result.published is True

    def test_create_resume_missing_file_returns_error(
        self,
        api_client: _FakeApiClient,
        tmp_path: Path,
    ) -> None:
        handler = ResumeCreateHandler(api_client=api_client)
        result = handler.create(
            template=tmp_path / "no_such_file.md",
            dry_run=False,
            publish=False,
        )
        assert result.ok is False
        assert result.error is not None


# ─── Clone-resume handler ────────────────────────────────────


class TestResumeCloneHandler:
    def test_clone_posts_resume_profile_payload(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.resumes = [{"id": "r-1", "title": "Python Dev"}]
        api_client.post_response = {"id": "cloned-1"}
        handler = ResumeCloneHandler(api_client=api_client)
        result = handler.clone(resume_id="r-1")

        # The wire format must match the legacy contract:
        #   POST /resume_profile  { clone_resume_id, additional_properties: { any_job: true } }
        posts = [c for c in api_client.captured if c[0] == "post"]
        assert len(posts) == 1
        post = posts[0][1]
        assert post["endpoint"] == "/resume_profile"
        assert post["as_json"] is True
        assert post["payload"] == {
            "additional_properties": {"any_job": True},
            "clone_resume_id": "r-1",
        }
        assert result.ok is True
        assert result.cloned_resume_id == "cloned-1"

    def test_clone_picks_first_resume_when_id_omitted(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.resumes = [
            {"id": "first", "title": "A"},
            {"id": "second", "title": "B"},
        ]
        handler = ResumeCloneHandler(api_client=api_client)
        handler.clone(resume_id=None)
        posts = [c for c in api_client.captured if c[0] == "post"]
        assert posts[0][1]["payload"]["clone_resume_id"] == "first"

    def test_clone_returns_error_when_no_resumes(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.resumes = []
        handler = ResumeCloneHandler(api_client=api_client)
        result = handler.clone(resume_id=None)
        assert result.ok is False
        assert "resume" in (result.error or "").lower()

    def test_clone_returns_error_when_id_unknown(
        self, api_client: _FakeApiClient
    ) -> None:
        api_client.resumes = [{"id": "r-1"}]
        handler = ResumeCloneHandler(api_client=api_client)
        result = handler.clone(resume_id="nope")
        assert result.ok is False


# ─── API client port (sanity) ─────────────────────────────────


def test_api_client_port_protocol_shape() -> None:
    """The :class:`HhApiClientPort` must expose ``get`` and ``post``."""
    client = MagicMock(spec=HhApiClientPort)
    client.get("/me")
    client.post("/x", {"a": 1}, as_json=True)
    client.get.assert_called_once_with("/me")
    client.post.assert_called_once_with("/x", {"a": 1}, as_json=True)
