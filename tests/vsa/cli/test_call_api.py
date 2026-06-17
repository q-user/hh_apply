"""Tests for the ``call_api`` VSA sub-command (issue #147)."""

from __future__ import annotations

import argparse
import json
from typing import Any

import pytest

from job_bot.cli.call_api import Operation


class _FakeApiClient:
    """Records every ``request`` call and returns a canned response."""

    def __init__(self, response: Any = None) -> None:
        self.response = response if response is not None else {"ok": True}
        self.calls: list[tuple[str, str, dict[str, Any], bool]] = []

    def request(
        self,
        method: str,
        endpoint: str,
        *,
        params: Any = None,
        as_json: bool = False,
    ) -> Any:
        self.calls.append((method, endpoint, dict(params or {}), as_json))
        return self.response


class _FakeSlice:
    def __init__(self, api_client: _FakeApiClient) -> None:
        self.api_client = api_client


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd").add_parser("call-api")
    Operation().setup_parser(sub)
    return parser


class TestCallApiSetupParser:
    def test_endpoint_is_positional(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["call-api", "/me"])
        assert ns.endpoint == "/me"

    def test_default_method_is_get(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["call-api", "/me"])
        assert ns.method == "GET"

    def test_method_can_be_overridden(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(["call-api", "/foo", "-X", "POST"])
        assert ns.method == "POST"

    def test_param_pairs_are_parsed(self) -> None:
        parser = _make_parser()
        ns = parser.parse_args(
            ["call-api", "/search", "text=python", "text=rust"]
        )
        assert ns.param == ["text=python", "text=rust"]


class TestCallApiRun:
    def test_get_request_is_dispatched(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api = _FakeApiClient(response={"id": "u-1"})
        op = Operation(slice_=_FakeSlice(api))

        parser = _make_parser()
        ns = parser.parse_args(["call-api", "/me"])
        rc = op.run(ns)

        assert rc == 0
        assert api.calls == [("GET", "/me", {}, False)]
        out = capsys.readouterr().out
        assert json.loads(out) == {"id": "u-1"}

    def test_param_pairs_become_query_dict(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api = _FakeApiClient()
        op = Operation(slice_=_FakeSlice(api))

        parser = _make_parser()
        ns = parser.parse_args(
            ["call-api", "/search", "text=python", "text=rust"]
        )
        op.run(ns)

        # Param pairs are collected into a list-valued dict.
        method, endpoint, params, _ = api.calls[0]
        assert method == "GET"
        assert endpoint == "/search"
        assert params == {"text": ["python", "rust"]}

    def test_data_json_is_parsed(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api = _FakeApiClient()
        op = Operation(slice_=_FakeSlice(api))

        parser = _make_parser()
        ns = parser.parse_args(
            [
                "call-api",
                "/negotiations",
                "-X",
                "POST",
                "-d",
                '{"vacancy_id": "v-1"}',
            ]
        )
        op.run(ns)

        method, endpoint, params, as_json = api.calls[0]
        assert method == "POST"
        assert endpoint == "/negotiations"
        assert params == {"vacancy_id": "v-1"}
        assert as_json is True

    def test_invalid_json_returns_1(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        api = _FakeApiClient()
        op = Operation(slice_=_FakeSlice(api))

        parser = _make_parser()
        ns = parser.parse_args(["call-api", "/foo", "-d", "not-json"])
        rc = op.run(ns)

        assert rc == 1
        assert api.calls == []  # No request dispatched.
