from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lib.leader_pr import GAP_LABEL, GhCommandError, LeaderPRManager
from lib.state_file import build_state


def _completed(stdout: str = "", returncode: int = 0) -> MagicMock:
    result = MagicMock()
    result.returncode = returncode
    result.stdout = stdout
    result.stderr = ""
    return result


def test_dry_run_prints_gh_pr_create_dry_run(capsys: pytest.CaptureFixture[str]) -> None:
    calls: list[list[str]] = []

    def fake_runner(command: list[str], cwd: Path | None) -> MagicMock:
        calls.append(list(command))
        if command[:3] == ["gh", "pr", "list"]:
            return _completed("[]\n")
        return _completed("")

    manager = LeaderPRManager(
        repo="red-hat-data-services/gated-artifacts-promoter",
        dry_run=True,
        runner=fake_runner,
    )
    state = build_state(
        trigger_id="gap-testtrigger001",
        prs=[],
        created_at="2026-09-21T12:00:00Z",
    )
    result = manager.create_or_update(state)

    captured = capsys.readouterr().out
    assert "[dry-run] gh pr create" in captured
    assert "--dry-run" in captured
    assert result.dry_run is True
    assert result.pr_url is None
    assert result.state_path == "gap-testtrigger001/state.json"
    assert any(cmd[:3] == ["gh", "pr", "list"] for cmd in calls)


def test_create_leader_pr_via_gh(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[list[str], str | None]] = []

    def fake_runner(command: list[str], cwd: Path | None) -> MagicMock:
        calls.append((list(command), str(cwd) if cwd else None))
        if command[:3] == ["gh", "pr", "list"]:
            return _completed("[]\n")
        if command[:3] == ["gh", "repo", "clone"]:
            dest = Path(command[4])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
            return _completed("")
        if command[0] == "git" and command[1] == "status":
            return _completed("A  gap-testtrigger002/state.json\n")
        if command[:3] == ["gh", "pr", "create"]:
            return _completed(
                "https://github.com/red-hat-data-services/gated-artifacts-promoter/pull/9\n"
            )
        return _completed("")

    manager = LeaderPRManager(
        repo="red-hat-data-services/gated-artifacts-promoter",
        runner=fake_runner,
    )
    state = build_state(
        trigger_id="gap-testtrigger002",
        prs=[
            {
                "name": "kserve",
                "repo": "rhoai-rhtap/kserve",
                "url": "https://github.com/rhoai-rhtap/kserve/pull/2",
                "number": 2,
            }
        ],
        created_at="2026-09-21T12:00:00Z",
    )
    result = manager.create_or_update(state)

    assert result.updated is False
    assert result.pr_number == 9
    assert result.pr_url.endswith("/pull/9")

    create_cmd = next(cmd for cmd, _ in calls if cmd[:3] == ["gh", "pr", "create"])
    assert "--label" in create_cmd
    assert "gap-testtrigger002" in create_cmd
    assert GAP_LABEL in create_cmd


def test_update_existing_leader_pr() -> None:
    def fake_runner(command: list[str], cwd: Path | None) -> MagicMock:
        if command[:3] == ["gh", "pr", "list"]:
            return _completed(
                json.dumps(
                    [
                        {
                            "number": 4,
                            "url": "https://github.com/red-hat-data-services/gated-artifacts-promoter/pull/4",
                            "headRefName": "gap-leader/gap-existing",
                            "title": "old",
                        }
                    ]
                )
            )
        if command[:3] == ["gh", "repo", "clone"]:
            dest = Path(command[4])
            dest.mkdir(parents=True, exist_ok=True)
            (dest / ".git").mkdir(exist_ok=True)
            return _completed("")
        if command[0] == "git" and command[1] == "status":
            return _completed("M  gap-existing/state.json\n")
        return _completed("")

    manager = LeaderPRManager(
        repo="red-hat-data-services/gated-artifacts-promoter",
        runner=fake_runner,
    )
    state = build_state(
        trigger_id="gap-existing",
        prs=[],
        created_at="2026-09-21T12:00:00Z",
    )
    result = manager.create_or_update(state)
    assert result.updated is True
    assert result.pr_number == 4


def test_gh_failure_raises() -> None:
    def fake_runner(command: list[str], cwd: Path | None) -> MagicMock:
        return _completed("boom", returncode=1)

    manager = LeaderPRManager(runner=fake_runner)
    with pytest.raises(GhCommandError):
        manager.find_open_pr_by_label("gap-x")
