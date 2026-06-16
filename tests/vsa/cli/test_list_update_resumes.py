"""Tests for the ``list_resumes`` and ``update_resumes`` VSA sub-commands
(issue #147).

Both ops are thin VSA adapters over the :class:`VacancySearchSlice`'s
``vacancies`` port (or its dedicated ``get_resumes`` helper).
"""

from __future__ import annotations

import argparse
from typing import Any

import pytest

from job_bot.cli.list_resumes import Operation as ListResumesOp
from job_bot.cli.update_resumes import Operation as UpdateResumesOp


class _FakeResume:
    def __init__(
        self,
        resume_id: str,
        title: str,
        *,
        can_publish: bool = True,
        alternate_url: str = "",
    ) -> None:
        self.id = resume_id
        self.title = title
        self.can_publish_or_update = can_publish
        self.alternate_url = (
            alternate_url or f"https://hh.ru/resume/{resume_id}"
        )
        self.status = {"name": "published"}


class _FakeResumesPort:
    def __init__(self, resumes: list[_FakeResume] | None = None) -> None:
        self._resumes = resumes or []
        self.saved_batches: list[list[_FakeResume]] = []
        self.publish_calls: list[str] = []

    def get_resumes(self) -> list[_FakeResume]:
        return list(self._resumes)

    def save_batch(self, items: list[_FakeResume]) -> None:
        self.saved_batches.append(list(items))

    def publish(self, resume_id: str) -> dict[str, Any]:
        self.publish_calls.append(resume_id)
        return {}


class _FakeApiClient:
    def __init__(self) -> None:
        self.posts: list[tuple[str, dict[str, Any] | None]] = []

    def post(
        self, endpoint: str, json_data: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        self.posts.append((endpoint, json_data))
        return {}


class _FakeSlice:
    def __init__(
        self,
        resumes: _FakeResumesPort | None = None,
        api_client: _FakeApiClient | None = None,
    ) -> None:
        self.vacancies = resumes or _FakeResumesPort()
        self.api_client = api_client or _FakeApiClient()


def _make_parser(op_cls: type, name: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser(name)
    op_cls().setup_parser(sub)
    return parser


class TestListResumes:
    def test_setup_parser_no_args(self) -> None:
        parser = _make_parser(ListResumesOp, "list-resumes")
        ns = parser.parse_args(["list-resumes"])
        assert ns.cmd == "list-resumes"

    def test_run_saves_and_prints_table(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resumes = _FakeResumesPort(
            [
                _FakeResume("r1", "Python Dev"),
                _FakeResume("r2", "Go Dev"),
            ]
        )
        op = ListResumesOp(slice_=_FakeSlice(resumes=resumes))

        parser = _make_parser(ListResumesOp, "list-resumes")
        ns = parser.parse_args(["list-resumes"])
        rc = op.run(ns)

        out = capsys.readouterr().out
        assert rc == 0
        # The batch was saved via the port.
        assert len(resumes.saved_batches) == 1
        assert [r.id for r in resumes.saved_batches[0]] == ["r1", "r2"]
        # The table contains the resume titles and IDs.
        assert "r1" in out
        assert "r2" in out


class TestUpdateResumes:
    def test_setup_parser(self) -> None:
        parser = _make_parser(UpdateResumesOp, "update-resumes")
        ns = parser.parse_args(
            ["update-resumes", "--search", "py", "--id", "r1"]
        )
        assert ns.search == "py"
        assert ns.id == "r1"

    def test_run_publishes_every_publishable(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resumes = _FakeResumesPort(
            [
                _FakeResume("r1", "Python", can_publish=True),
                _FakeResume("r2", "Go", can_publish=False),
                _FakeResume("r3", "Rust", can_publish=True),
            ]
        )
        op = UpdateResumesOp(slice_=_FakeSlice(resumes=resumes))

        parser = _make_parser(UpdateResumesOp, "update-resumes")
        ns = parser.parse_args(["update-resumes"])
        rc = op.run(ns)

        assert rc == 0
        # Only r1 and r3 are publishable; r2 is skipped.
        assert resumes.publish_calls == ["r1", "r3"]

    def test_search_filter_narrows(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resumes = _FakeResumesPort(
            [
                _FakeResume("r1", "Python Dev"),
                _FakeResume("r2", "Go Dev"),
            ]
        )
        op = UpdateResumesOp(slice_=_FakeSlice(resumes=resumes))

        parser = _make_parser(UpdateResumesOp, "update-resumes")
        ns = parser.parse_args(["update-resumes", "--search", "python"])
        op.run(ns)

        assert resumes.publish_calls == ["r1"]

    def test_id_filter_narrows(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        resumes = _FakeResumesPort(
            [
                _FakeResume("r1", "Python"),
                _FakeResume("r2", "Go"),
            ]
        )
        op = UpdateResumesOp(slice_=_FakeSlice(resumes=resumes))

        parser = _make_parser(UpdateResumesOp, "update-resumes")
        ns = parser.parse_args(["update-resumes", "--id", "r2"])
        op.run(ns)

        assert resumes.publish_calls == ["r2"]
