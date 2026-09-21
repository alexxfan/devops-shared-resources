from __future__ import annotations

import json
from pathlib import Path

import pytest

from lib.state_file import (
    SCHEMA_VERSION,
    StateFileError,
    StatePullRequest,
    build_state,
    load_state_file,
    parse_pr_number_from_url,
    repo_slug_from_url,
    validate_state,
    write_state_file,
)


def test_build_and_validate_state() -> None:
    state = build_state(
        trigger_id="gap-abc123",
        prs=[
            {
                "name": "kserve",
                "repo": "rhoai-rhtap/kserve",
                "url": "https://github.com/rhoai-rhtap/kserve/pull/2",
                "number": 2,
            }
        ],
        leader_repo="red-hat-data-services/gated-artifacts-promoter",
        created_at="2026-09-21T10:00:00Z",
    )
    payload = state.to_dict()
    assert payload["schema_version"] == SCHEMA_VERSION
    assert payload["trigger_id"] == "gap-abc123"
    assert payload["leader"]["path"] == "gap-abc123/state.json"
    assert payload["prs"][0]["name"] == "kserve"
    assert validate_state(payload)["prs"][0]["number"] == 2


def test_write_and_load_state_file(tmp_path: Path) -> None:
    state = build_state(
        trigger_id="gap-deadbeef",
        prs=[
            StatePullRequest(
                name="kubeflow",
                repo="rhoai-rhtap/kubeflow",
                url="https://github.com/rhoai-rhtap/kubeflow/pull/81",
                number=81,
            )
        ],
        created_at="2026-09-21T11:00:00Z",
    )
    path = write_state_file(tmp_path / "gap-deadbeef" / "state.json", state)
    loaded = load_state_file(path)
    assert loaded["prs"][0]["url"].endswith("/pull/81")
    assert json.loads(path.read_text(encoding="utf-8"))["trigger_id"] == "gap-deadbeef"


def test_validate_state_rejects_bad_schema() -> None:
    with pytest.raises(StateFileError, match="schema_version"):
        validate_state({"schema_version": 99, "trigger_id": "x", "created_at": "t", "leader": {}, "prs": []})


def test_validate_state_rejects_path_mismatch() -> None:
    with pytest.raises(StateFileError, match="leader.path"):
        validate_state(
            {
                "schema_version": 1,
                "trigger_id": "gap-1",
                "created_at": "2026-01-01T00:00:00Z",
                "leader": {
                    "repo": "red-hat-data-services/gated-artifacts-promoter",
                    "path": "wrong/state.json",
                },
                "prs": [],
            }
        )


def test_repo_slug_and_pr_number_helpers() -> None:
    assert repo_slug_from_url("https://github.com/org/repo.git") == "org/repo"
    assert repo_slug_from_url("git@github.com:org/repo.git") == "org/repo"
    assert parse_pr_number_from_url("https://github.com/org/repo/pull/12") == 12
