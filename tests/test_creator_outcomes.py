"""Creator observations survive failures/retries and share the Leader summary."""
import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from lib.creator_outcomes import merge_outcomes, observation, safe_reason, validate_outcomes
from lib.leader_pr import GAP_LABEL, LeaderPRManager, LeaderPRResult
from lib.pr_creator import PRCreator, PullRequestOperationError
from lib.state_file import StatePullRequest, build_state, load_state_file, write_state_file
from scripts import gap_failure_reporter as fr
from scripts import run_gated_artifacts_promoter as promoter
from scripts.sync_branches import SyncOutcome

LEADER = "https://github.com/acme/leaders/pull/9"
URL = "https://github.com/acme/foo/pull/2"


def record(outcome="creation-failure", *, repo="acme/foo", pr_url=""):
    return observation(repo, "main", "stable", outcome, "Recorded reason", pr_url=pr_url)


def fake_leader():
    manager = MagicMock()
    manager.create_or_update.return_value = LeaderPRResult(
        trigger_id="gap-test", repo="acme/leaders", branch="gap-leader/gap-test",
        state_path="GAP Leaders/2026-11-04_gap-test/state.json",
        pr_url=LEADER, pr_number=9, updated=False, dry_run=False,
    )
    return manager


def config(tmp_path, names):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"git": [
        {"name": name, "repo-url": f"https://github.com/acme/{name}.git",
         "automerge": "no" if name == "manual" else "yes"} for name in names]}))
    return path


def test_failed_repo_does_not_abort_others_and_is_saved_without_a_pr(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_REPOSITORY", "acme/infra")
    monkeypatch.setenv("GITHUB_RUN_ID", "123")
    path = config(tmp_path, ["private", "manual", "disabled", "up-to-date"])
    seen, progress = [], []
    def sync(entry, **kwargs):
        name = entry["name"]
        seen.append(name)
        if name == "private":
            raise RuntimeError("Repository not found (404)")
        if name == "up-to-date":
            return SyncOutcome(sync_type="pr", message="No changes to sync")
        return SyncOutcome(sync_type="pr", message="Created PR",
                           pr_url=f"https://github.com/acme/{name}/pull/2",
                           automerge_skipped="Auto-merge is not allowed for this repository" if name == "disabled" else None)
    manager = fake_leader()
    result = promoter.run_promoter(config_path=path, trigger_id="gap-test", token="unused",
                                   sync_runner=sync, leader_manager=manager, progress=progress.append)
    assert seen == ["private", "manual", "disabled", "up-to-date"]
    state = manager.create_or_update.call_args.args[0]
    records = {r["repo"]: r for r in state.creator_outcomes}
    assert records["acme/private"]["outcome"] == "creation-failure"
    assert records["acme/private"]["pr-url"] == ""
    assert records["acme/private"]["workflow-url"].endswith("/actions/runs/123")
    assert records["acme/manual"]["outcome"] == "manual-merge"
    assert records["acme/disabled"]["outcome"] == "automerge-unavailable"
    assert records["acme/up-to-date"]["outcome"] == "success"
    assert len(result.failures) == 1
    assert [p.repo for p in state.pull_requests] == ["manual", "disabled"]
    manager.publish_creator_outcomes.assert_called_once_with(result.leader, state)


def test_post_create_api_error_keeps_pr_url_and_is_not_disallowed_automerge(tmp_path):
    manager = fake_leader()
    def sync(entry, **kwargs):
        raise PullRequestOperationError("GitHub API failed (403)", URL)
    result = promoter.run_promoter(config_path=config(tmp_path, ["foo"]), trigger_id="gap-test",
                                   token="unused", sync_runner=sync, leader_manager=manager)
    assert result.failures[0]["outcome"] == "pr-operation-failure"
    assert result.failures[0]["pr-url"] == URL
    assert result.state_prs[0].pr_url == URL
    assert all(r["outcome"] != "automerge-unavailable" for r in result.failures)


def test_workflow_exits_failure_only_after_leader_and_report_are_saved(tmp_path, monkeypatch):
    manager = fake_leader()
    def sync(entry, **kwargs):
        raise RuntimeError("Creation denied")
    result = promoter.run_promoter(config_path=config(tmp_path, ["foo"]), trigger_id="gap-test",
                                   token="unused", sync_runner=sync, leader_manager=manager)
    monkeypatch.setattr(promoter, "run_promoter", lambda **kwargs: result)
    assert promoter.main(["--config", "unused"]) == 1
    manager.create_or_update.assert_called_once()
    manager.publish_creator_outcomes.assert_called_once()


def test_credentials_are_not_persisted_or_reported(tmp_path):
    secret = "test-secret-value-abcdefghijklmnopqrstuvwxyz"
    progress = []
    def sync(entry, **kwargs):
        raise RuntimeError(f"clone https://x-access-token:{secret}@github.com/acme/foo.git\nAuthorization: Bearer {secret}")
    manager = fake_leader()
    result = promoter.run_promoter(config_path=config(tmp_path, ["foo"]), trigger_id="gap-test",
                                   token=secret, sync_runner=sync, leader_manager=manager,
                                   progress=progress.append)
    state = manager.create_or_update.call_args.args[0].to_json()
    combined = state + json.dumps(progress) + fr.render_creator_outcomes(result.failures)
    assert secret not in combined
    assert "x-access-token:" not in combined
    assert "[redacted]" in combined


@pytest.mark.parametrize("existing", [False, True])
def test_pr_helper_keeps_context_when_followup_api_operation_fails(existing):
    creator = PRCreator("unused")
    creator._request = MagicMock(return_value={"number": 2, "html_url": URL})
    creator.add_labels = MagicMock(side_effect=RuntimeError("labels API denied"))
    with pytest.raises(PullRequestOperationError) as raised:
        if existing:
            creator.find_open_pr_by_head = MagicMock(return_value={
                "number": 2, "html_url": URL, "head": {"ref": "main"}})
            creator.update_pull_request = MagicMock()
            creator.create_or_update_tracking_pr("https://github.com/acme/foo",
                title="Sync", body="body", head_branch="main", base_branch="stable",
                tracking_label=GAP_LABEL)
        else:
            creator.create_pull_request("https://github.com/acme/foo",
                title="Sync", body="body", head_branch="main", base_branch="stable",
                labels=[GAP_LABEL], delete_branch_on_merge=False)
    assert raised.value.pr_url == URL
    assert str(raised.value) == "labels API denied"


def test_state_roundtrip_and_partial_retry_preserve_outcomes(tmp_path):
    older = record()
    other = record(repo="acme/other")
    repaired = record("success", pr_url=URL)
    records = merge_outcomes([older, other], [repaired])
    assert {r["repo"]: r["outcome"] for r in records} == {
        "acme/foo": "success", "acme/other": "creation-failure"}
    state = build_state(pull_requests=[], creator_outcomes=records)
    path = tmp_path / "state.json"
    write_state_file(path, state)
    assert load_state_file(path)["creator-outcomes"] == records
    # Status-only monitors preserve this new field with their existing load/save.
    from scripts.gap_pr_monitor import load_state, save_state
    payload = load_state(path)
    payload["overall-status"] = "failure"
    save_state(path, payload)
    assert load_state_file(path)["creator-outcomes"] == records


@pytest.mark.parametrize("change", ["outcome", "repo", "pr-url", "workflow-url", "observed-at"])
def test_invalid_observation_is_not_silently_dropped(change):
    item = record()
    item[change] = {"outcome": "unknown", "repo": "invalid",
                    "pr-url": "https://github.com/acme/other/pull/2",
                    "workflow-url": "https://untrusted.test/run",
                    "observed-at": "invalid"}[change]
    with pytest.raises(ValueError):
        validate_outcomes([item])


def comment_api(initial=None):
    comments = [] if initial is None else [{"id": 55, "body": initial}]
    calls = []
    def gh(args):
        calls.append(list(args))
        if "--paginate" in args:
            return json.dumps([comments])
        if "--input" in args:
            body = json.loads(Path(args[args.index("--input") + 1]).read_text())["body"]
            if "PATCH" in args:
                comments[0]["body"] = body
            elif "POST" in args:
                comments.append({"id": 55, "body": body})
            return json.dumps({"id": 55})
        raise AssertionError(args)
    return gh, comments, calls


def test_creator_section_uses_one_comment_and_preserves_build_summary():
    original = fr.SUMMARY_MARKER + "\n## Build status\n| foo | build-failure |\n"
    gh, comments, calls = comment_api(original)
    fr.publish_creator_outcomes(LEADER, [record()], gh_runner=gh)
    first = comments[0]["body"]
    assert original.strip() in first
    assert "creation-failure" in first and "| — |" in first
    fr.publish_creator_outcomes(LEADER, [record("success", pr_url=URL)], gh_runner=gh)
    assert len(comments) == 1
    assert original.strip() in comments[0]["body"]
    assert "creation-failure" not in comments[0]["body"]
    assert "No outstanding" in comments[0]["body"]
    assert sum("PATCH" in c for c in calls) == 2
    assert not any("POST" in c for c in calls)


def test_creation_without_any_child_still_creates_a_report():
    gh, comments, calls = comment_api()
    fr.publish_creator_outcomes(LEADER, [record()], gh_runner=gh)
    fr.publish_creator_outcomes(LEADER, [record()], gh_runner=gh)
    assert len(comments) == 1
    assert comments[0]["body"].count(fr.SUMMARY_MARKER) == 1
    assert "acme/foo" in comments[0]["body"]
    assert "creation-failure" in comments[0]["body"]
    assert sum("POST" in c for c in calls) == 1


def test_dry_run_never_writes_a_comment():
    gh, comments, calls = comment_api()
    fr.publish_creator_outcomes(LEADER, [record()], dry_run=True, gh_runner=gh)
    assert comments == []
    assert not any("POST" in c or "PATCH" in c for c in calls)


@pytest.mark.parametrize("resolution", ["merged", "enabled", "lookup_failed"])
def test_reporter_resolves_or_preserves_auto_merge_warning(monkeypatch, resolution):
    item = record("automerge-unavailable", pr_url=URL)
    def fetch(*args, **kwargs):
        if resolution == "lookup_failed":
            raise fr.GapPrMonitorError("lookup unavailable")
        return {"mergedAt": "2026-11-04" if resolution == "merged" else None,
                "autoMergeRequest": {} if resolution == "enabled" else None}
    # GitHub returns a nonempty autoMergeRequest object when enabled.
    if resolution == "enabled":
        monkeypatch.setattr(fr, "fetch_pull_request", lambda *a, **k: {"autoMergeRequest": {"enabledAt": "2026-11-04"}})
    else:
        monkeypatch.setattr(fr, "fetch_pull_request", fetch)
    result = fr.current_creator_outcomes([item])
    if resolution == "lookup_failed":
        assert result[0]["outcome"] == "automerge-unavailable"
        assert "lookup unavailable" in result[0]["reason"]
    else:
        assert result == []


def test_periodic_report_reads_persisted_creation_failure(tmp_path, monkeypatch):
    payload = {"pull-requests": [], "creator-outcomes": [record()]}
    path = tmp_path / "state.json"
    path.write_text(json.dumps(payload))
    gh, comments, calls = comment_api()
    result = fr.report_for_leader(leader_pr_url=LEADER, repo_root=tmp_path,
                                  state_file=str(path), gh_runner=gh)
    assert result.created
    assert len(comments) == 1
    assert "creation-failure" in comments[0]["body"]
    assert path.read_text() == json.dumps(payload)


def test_partial_creator_retry_keeps_unattempted_child_progress(tmp_path, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "unused")
    previous = build_state(
        pull_requests=[{"repo": "foo", "pr-url": URL, "pr-status": "build-failure"},
                       {"repo": "other", "pr-url": "https://github.com/acme/other/pull/3",
                        "pr-status": "build-success"}],
        creator_outcomes=[record(), record(repo="acme/other")],
    ).to_dict()
    captured = {}
    def completed(text=""):
        value = MagicMock()
        value.returncode, value.stdout, value.stderr = 0, text, ""
        return value
    def gh_git(command, cwd):
        if command[:3] == ["gh", "pr", "list"]:
            return completed(json.dumps([{"number": 9, "url": LEADER,
                                          "headRefName": "gap-leader/gap-test"}]))
        if command[:3] == ["gh", "repo", "clone"]:
            Path(command[4]).mkdir(parents=True, exist_ok=True)
        if command[:2] == ["git", "checkout"]:
            old = Path(cwd) / "GAP Leaders/2026-11-04_gap-test/state.json"
            old.parent.mkdir(parents=True)
            old.write_text(json.dumps(previous))
        if command[:2] == ["git", "add"]:
            captured.update(json.loads((Path(cwd) / command[-1]).read_text()))
        if command[:2] == ["git", "status"]:
            return completed("M state.json")
        return completed()
    state = build_state(
        pull_requests=[StatePullRequest(repo="foo", pr_url=URL)],
        creator_outcomes=[record("success", pr_url=URL)],
    )
    manager = LeaderPRManager(repo="acme/leaders", runner=gh_git)
    manager.create_or_update(state, trigger_id="gap-test")
    assert {p["repo"]: p["pr-status"] for p in captured["pull-requests"]} == {
        "foo": "new", "other": "build-success"}
    assert {r["repo"]: r["outcome"] for r in captured["creator-outcomes"]} == {
        "acme/foo": "success", "acme/other": "creation-failure"}


def test_manual_policy_does_not_become_workflow_failure(tmp_path, monkeypatch):
    manager = fake_leader()
    result = promoter.run_promoter(
        config_path=config(tmp_path, ["manual"]), trigger_id="gap-test",
        token="unused", leader_manager=manager,
        sync_runner=lambda *a, **k: SyncOutcome(sync_type="pr", message="Created",
                                                pr_url="https://github.com/acme/manual/pull/2"),
    )
    assert result.failures == []
    monkeypatch.setattr(promoter, "run_promoter", lambda **kwargs: result)
    assert promoter.main(["--config", "unused"]) == 0


def test_no_change_repo_does_not_claim_child_creation_failed(tmp_path):
    manager = fake_leader()
    result = promoter.run_promoter(
        config_path=config(tmp_path, ["foo"]), trigger_id="gap-test",
        token="unused", leader_manager=manager,
        sync_runner=lambda *a, **k: SyncOutcome(sync_type="pr", message="No commits to sync"),
    )
    assert result.failures == []
    assert result.state_prs == []
    state = manager.create_or_update.call_args.args[0]
    assert state.creator_outcomes[0]["outcome"] == "success"
    assert "creation-failure" not in fr.render_creator_outcomes(state.creator_outcomes)


def test_config_aliases_do_not_duplicate_or_drop_repository_outcomes(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text(yaml.safe_dump({"git": [
        {"name": "first-alias", "repo-url": "https://github.com/acme/foo.git", "automerge": "yes"},
        {"name": "second-alias", "repo-url": "https://github.com/acme/foo.git", "automerge": "yes"}]}))
    attempts = []
    def sync(entry, **kwargs):
        attempts.append(entry["name"])
        if len(attempts) == 1:
            raise RuntimeError("Transient access failure")
        return SyncOutcome(sync_type="pr", message="Updated", pr_url=URL)
    manager = fake_leader()
    result = promoter.run_promoter(config_path=path, trigger_id="gap-test", token="unused",
                                   sync_runner=sync, leader_manager=manager)
    assert attempts == ["first-alias", "second-alias"]
    records = manager.create_or_update.call_args.args[0].creator_outcomes
    assert len(records) == 1 and records[0]["outcome"] == "success"
    assert result.failures == []
