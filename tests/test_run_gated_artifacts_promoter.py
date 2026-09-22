from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import yaml

from lib.leader_pr import GAP_LABEL, LeaderPRResult
from lib.state_file import PR_STATUS_NEW, StatePullRequest
from scripts.run_gated_artifacts_promoter import (
    outcome_to_state_pr,
    prepare_entry_for_trigger,
    run_promoter,
)
from scripts.sync_branches import SyncOutcome


def test_prepare_entry_for_trigger_injects_labels() -> None:
    entry = {
        "name": "kserve",
        "sync_type": "pr",
        "src": {"url": "https://github.com/org/kserve.git", "branch": "main"},
        "dest": {"url": "https://github.com/org/kserve.git", "branch": "stable"},
        "ignore_files": ["README.md"],
        "pr": {
            "branch": "old-sync",
            "head_strategy": "sync-branch",
            "tracking_label": "lake-gate",
            "labels": ["existing"],
            "automerge": False,
            "title": None,
            "body": None,
            "reviewers": [],
        },
        "merge_args": [],
        "fetch_args": [],
        "push_args": [],
    }
    prepared = prepare_entry_for_trigger(entry, "gap-triggerxyz")
    assert prepared["pr"]["tracking_label"] == GAP_LABEL
    assert prepared["pr"]["head_strategy"] == "source"
    assert prepared["pr"]["branch"] is None
    assert prepared["ignore_files"] == [".tekton/*"]
    assert prepared["pr"]["labels"] == ["existing", GAP_LABEL, "gap-triggerxyz"]
    assert entry["pr"]["tracking_label"] == "lake-gate"
    assert entry["pr"]["head_strategy"] == "sync-branch"
    assert entry["ignore_files"] == ["README.md"]


def test_prepare_entry_always_ignores_tekton() -> None:
    entry = {
        "name": "kserve",
        "ignore_files": [],
        "pr": {"head_strategy": "sync-branch", "labels": []},
        "src": {"url": "https://github.com/org/kserve.git", "branch": "main"},
        "dest": {"url": "https://github.com/org/kserve.git", "branch": "stable"},
    }
    prepared = prepare_entry_for_trigger(entry, "gap-abc")
    assert prepared["ignore_files"] == [".tekton/*"]


def test_outcome_to_state_pr() -> None:
    entry = {
        "name": "kserve",
        "dest": {"url": "https://github.com/rhoai-rhtap/kserve.git", "branch": "stable"},
    }
    outcome = SyncOutcome(
        sync_type="pr",
        message="Created pull request #2",
        pr_url="https://github.com/rhoai-rhtap/kserve/pull/2",
    )
    state_pr = outcome_to_state_pr(entry, outcome)
    assert state_pr == StatePullRequest(
        repo="kserve",
        pr_url="https://github.com/rhoai-rhtap/kserve/pull/2",
        pr_status=PR_STATUS_NEW,
        builds=(),
    )


def test_run_promoter_dry_run(tmp_path: Path) -> None:
    config = {
        "defaults": {
            "source-branch": "main",
            "target-branch": "stable",
            "pr-head": "sync-branch",
        },
        "git": [
            {
                "name": "kserve",
                "automerge": "no",
                "repo-url": "https://github.com/rhoai-rhtap/kserve.git",
            }
        ],
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    sync_calls: list[dict] = []

    def fake_sync(entry, *, token, dry_run, verbose):
        sync_calls.append(entry)
        return SyncOutcome(
            sync_type="pr",
            message="Dry run: would sync",
            branch="sync-main-to-stable",
        )

    leader = MagicMock()
    leader.create_or_update.return_value = LeaderPRResult(
        trigger_id="gap-fixed",
        repo="red-hat-data-services/gated-artifacts-promoter",
        branch="gap-leader/gap-fixed",
        state_path="state/gap-fixed/state.json",
        pr_url=None,
        pr_number=None,
        updated=False,
        dry_run=True,
    )

    result = run_promoter(
        config_path=config_path,
        trigger_id="gap-fixed",
        dry_run=True,
        sync_runner=fake_sync,
        leader_manager=leader,
        token="unused",
    )

    assert result.trigger_id == "gap-fixed"
    assert len(sync_calls) == 1
    assert sync_calls[0]["pr"]["tracking_label"] == GAP_LABEL
    assert sync_calls[0]["pr"]["head_strategy"] == "source"
    assert sync_calls[0]["pr"]["branch"] is None
    assert sync_calls[0]["ignore_files"] == [".tekton/*"]
    assert GAP_LABEL in sync_calls[0]["pr"]["labels"]
    assert "gap-fixed" in sync_calls[0]["pr"]["labels"]
    assert result.state_prs == []
    leader.create_or_update.assert_called_once()
    _, kwargs = leader.create_or_update.call_args
    assert kwargs["trigger_id"] == "gap-fixed"


def test_run_promoter_collects_prs(tmp_path: Path) -> None:
    config = {
        "git": [
            {
                "name": "kserve",
                "automerge": "no",
                "repo-url": "https://github.com/rhoai-rhtap/kserve.git",
            }
        ]
    }
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config), encoding="utf-8")

    def fake_sync(entry, *, token, dry_run, verbose):
        return SyncOutcome(
            sync_type="pr",
            message="Created pull request #2",
            pr_url="https://github.com/rhoai-rhtap/kserve/pull/2",
        )

    leader = MagicMock()
    leader.create_or_update.return_value = LeaderPRResult(
        trigger_id="gap-live",
        repo="red-hat-data-services/gated-artifacts-promoter",
        branch="gap-leader/gap-live",
        state_path="state/gap-live/state.json",
        pr_url="https://github.com/red-hat-data-services/gated-artifacts-promoter/pull/1",
        pr_number=1,
        updated=False,
        dry_run=False,
    )

    result = run_promoter(
        config_path=config_path,
        trigger_id="gap-live",
        dry_run=False,
        sync_runner=fake_sync,
        leader_manager=leader,
        token="tok",
    )
    assert len(result.state_prs) == 1
    assert result.state_prs[0].repo == "kserve"
    assert result.state_prs[0].pr_status == PR_STATUS_NEW
    state_arg = leader.create_or_update.call_args.args[0]
    assert state_arg.pull_requests[0].pr_url.endswith("/pull/2")
    assert state_arg.to_dict() == {
        "pull-requests": [
            {
                "repo": "kserve",
                "pr-url": "https://github.com/rhoai-rhtap/kserve/pull/2",
                "pr-status": "new",
                "builds": [],
            }
        ]
    }
