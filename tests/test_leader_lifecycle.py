"""Retirement keeps history durable without granting child merge permission."""

import copy
import json
import subprocess
from types import SimpleNamespace

import pytest

from lib.leader_lifecycle import KEY, retire_leader, retired_outcome, validate_replacement
from lib.leader_history import write_history_snapshot
from lib.state_file import load_state_file
from lib.gap_promotion_gates import children_ready_for_success_status, should_trigger_early_gate
from scripts import gap_pr_monitor as monitor
from scripts import gap_early_gate_results as results
from scripts import gap_failure_reporter as reporter

OLD = "https://github.com/acme/gap/pull/9"
NEW = "https://github.com/acme/gap/pull/10"
SHA = "a" * 40


def payload(status="build-pending"):
    return {
        "pull-requests": [{
            "repo": "component", "pr-url": "https://github.com/acme/component/pull/3",
            "pr-status": status, "builds": [{"component": "model", "image": "quay.io/acme/model@sha256:123"}],
            "build-observations": {"source": "konflux", "runs": [], "head-sha": "b" * 40},
        }],
        "creator-outcomes": [],
        "promotion-gates": [
            {"id": "component-builds", "status": "pending", "required": True},
            {"id": "early-gate-build", "status": "pending", "required": False},
            {"id": "early-gate-test", "status": "pending", "required": False},
        ],
    }


def retired(data=None):
    data = copy.deepcopy(data if data is not None else payload())
    retire_leader(data, replacement_url=NEW, source_sha=SHA)
    return data


def pull(number=9, sha=SHA):
    trigger = f"gap-{number}"
    return {
        "number": number, "state": "open", "merged": False, "title": f"GAP leader: {trigger}",
        "labels": [{"name": name} for name in ["gated-artifacts-promoter", trigger]],
        "head": {"sha": sha, "ref": f"gap-leader/{trigger}", "repo": {"full_name": "acme/gap"}},
        "base": {"ref": "main", "repo": {"full_name": "acme/gap"}},
    }


@pytest.mark.parametrize("statuses,expected,folder", [
    (["pending", "pending", "pending"], "superseded", "unsuccessful"),
    (["success", "success", "pending"], "superseded", "unsuccessful"),
    (["failure", "pending", "pending"], "failure", "unsuccessful"),
    (["success", "failure", "pending"], "failure", "unsuccessful"),
    (["success", "success", "success"], "success", "successful"),
])
def test_retirement_preserves_actual_results_and_history(tmp_path, statuses, expected, folder):
    data = payload()
    for gate, status in zip(data["promotion-gates"], statuses):
        gate["status"] = status
    original = copy.deepcopy(data)
    assert retire_leader(data, replacement_url=NEW, source_sha=SHA) == expected
    assert {k: v for k, v in data.items() if k != KEY} == original
    assert data[KEY]["replacement-leader"] == NEW
    path = tmp_path / "GAP Leaders/2026-11-05_gap-9/state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(data))
    snapshot = write_history_snapshot(tmp_path, path)
    assert snapshot == tmp_path / f"GAP History/{folder}/2026-11-05_gap-9/state.json"
    assert json.loads(snapshot.read_text()) == data
    assert load_state_file(path)[KEY] == data[KEY]
    assert load_state_file(path)["promotion-gates"] == original["promotion-gates"]
    first = copy.deepcopy(data)
    # A later replacement or retry does not rewrite the original retirement.
    retire_leader(data, replacement_url=NEW.replace("/10", "/11"), source_sha="c" * 40)
    assert data == first


@pytest.mark.parametrize("bad", [None, {}, {"status": "active"},
    {"status": "retired", "outcome": "success"}, {"status": "retired", "outcome": []}])
def test_malformed_metadata_cannot_reactivate_leader(bad):
    with pytest.raises((ValueError, TypeError)):
        retired_outcome({KEY: bad})


@pytest.mark.parametrize("mutation", ["closed", "merged", "fork", "base", "trigger", "draft", "same-batch", "reversed", "other-repo"])
def test_replacement_validation_rejects_unrelated_or_stale_leaders(mutation):
    old, new, url = pull(), pull(10), NEW
    if mutation == "closed":
        new["state"] = "closed"
    elif mutation == "merged":
        new["merged"] = True
    elif mutation == "fork":
        new["head"]["repo"]["full_name"] = "outsider/gap"
    elif mutation == "base":
        new["base"]["ref"] = "release"
    elif mutation == "trigger":
        new["labels"].pop()
    elif mutation == "draft":
        new["draft"] = True
    elif mutation == "same-batch":
        new = copy.deepcopy(old)
    elif mutation == "reversed":
        url = OLD.replace("/9", "/8")
    else:
        url = NEW.replace("acme", "other")
    with pytest.raises(ValueError):
        validate_replacement(OLD, old, url, new)


def test_valid_replacement():
    validate_replacement(OLD, pull(), NEW, pull(10))


def test_late_events_cannot_change_retired_run(tmp_path):
    data = retired()
    path = tmp_path / "state.json"
    path.write_text(json.dumps(data))
    before = path.read_bytes()

    def forbidden(*args, **kwargs):
        pytest.fail("Retired runs must not query builds, comments or post statuses")

    outcome = monitor.run_monitor(path, gh_runner=forbidden, updater_factory=forbidden)
    assert outcome.overall_status == "superseded"
    assert not outcome.updated_urls
    outcome = results.run_early_gate_results(
        repo_root=tmp_path, state_file=str(path), leader_pr_url=OLD,
        labels=[], gh_runner=forbidden, updater_factory=forbidden,
    )
    assert not outcome.applied
    assert "retired" in outcome.reason
    report = reporter.report_for_leader(
        repo_root=tmp_path, state_file=str(path), leader_pr_url=OLD, gh_runner=forbidden,
    )
    assert not report.created and not report.updated
    assert results.child_pass_pr_urls(retired(payload("success"))) == []
    assert not children_ready_for_success_status(data)
    assert not should_trigger_early_gate(data)
    assert results.decide_overall_from_comments(
        payload=data, comments=[{"body": "Early Gate passed"}], labels=["early-gate-build-success"],
    )[0] is None
    assert path.read_bytes() == before


@pytest.fixture
def git_leader(tmp_path):
    def git(args, cwd):
        run = subprocess.run(
            ["git", "-c", "commit.gpgsign=false", *args], cwd=cwd,
            capture_output=True, text=True,
        )
        if run.returncode:
            raise monitor.GapPrMonitorError(run.stderr)
        return run.stdout.strip()
    remote, root = tmp_path / "origin.git", tmp_path / "checkout"
    git(["init", "--bare", str(remote)], tmp_path)
    git(["clone", str(remote), str(root)], tmp_path)
    git(["config", "user.name", "Test"], root)
    git(["config", "user.email", "test@example.com"], root)
    git(["checkout", "-b", "gap-leader/gap-9"], root)
    path = root / "GAP Leaders/2026-11-05_gap-9/state.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(payload()))
    git(["add", "."], root)
    git(["commit", "-m", "Pending run"], root)
    git(["push", "-u", "origin", "HEAD"], root)
    return root, path, git


@pytest.mark.parametrize("block_merge", [False, True])
def test_rollover_publishes_durable_history_before_merge_and_can_retry(git_leader, block_merge):
    root, path, git = git_leader
    calls, status_posts = [], []
    initial = json.loads(path.read_text())
    original_sha = git(["rev-parse", "HEAD"], root)
    blocked = block_merge

    def head():
        return git(["rev-parse", "origin/gap-leader/gap-9"], root)

    def gh(args):
        calls.append(args)
        if args == ["api", "repos/acme/gap/pulls/9"]:
            return json.dumps(pull(sha=head()))
        if args == ["api", "repos/acme/gap/pulls/10"]:
            return json.dumps(pull(10))
        if args[:2] == ["pr", "merge"]:
            assert args[-1] == head()
            if blocked:
                raise RuntimeError("merge blocked by repository policy")
        return ""

    class Updater:
        dry_run = False
        def __init__(self, **kwargs): pass
        def get_pull_head(self, ref):
            return SimpleNamespace(sha=head())
        def post_status_for_pr(self, url, state, **kwargs):
            status_posts.append((url, state, kwargs))
            assert url == OLD
            return SimpleNamespace(skipped=False)

    kwargs = dict(
        repo_root=root, head_ref="gap-leader/gap-9", leader_pr_url=OLD,
        replacement_url=NEW, publish=True, updater_factory=Updater,
        gh_runner=gh, git_runner=git,
    )
    if blocked:
        with pytest.raises(RuntimeError, match="merge blocked"):
            monitor.run_monitor_publish_and_signal(path, **kwargs)
        assert not status_posts
    else:
        result = monitor.run_monitor_publish_and_signal(path, **kwargs)
        assert result.leader_status_posted
    data = json.loads(path.read_text())
    assert data["pull-requests"] == initial["pull-requests"]
    assert data["promotion-gates"] == initial["promotion-gates"]
    assert data[KEY]["source-head"] == original_sha
    archive = "GAP History/unsuccessful/2026-11-05_gap-9/state.json"
    assert json.loads(git(["show", f"{head()}:{archive}"], root)) == data
    assert any("title=[GAP superseded] GAP leader: gap-9" in c for c in calls)
    assert not any("--admin" in c or "--delete-branch" in c or c[:2] == ["pr", "close"] for c in calls)
    first_sha = head()
    blocked = False
    kwargs["replacement_url"] = None
    monitor.run_monitor_publish_and_signal(path, **kwargs)
    assert head() == first_sha
    assert json.loads(path.read_text()) == data
    assert all(url == OLD for url, _, _ in status_posts)


def test_moved_head_blocks_retirement_before_writing(git_leader):
    root, path, git = git_leader
    before = path.read_bytes()
    with pytest.raises(monitor.GapPrMonitorError, match="changed before rollover"):
        monitor.retire_previous_leader(
            path, repo_root=root, head_ref="gap-leader/gap-9",
            leader_pr_url=OLD, replacement_url=NEW, check_name="gap",
            dry_run=False, remote="origin", gh_runner=lambda _: json.dumps(pull()),
            git_runner=git,
        )
    assert path.read_bytes() == before
    assert not (root / "GAP History").exists()


def test_retirement_push_conflict_is_not_rebased_or_signalled(git_leader):
    root, path, git = git_leader
    commands = []
    old_sha = git(["rev-parse", "HEAD"], root)
    def conflicting_git(args, cwd):
        commands.append(args)
        if args[0] == "push":
            raise monitor.GapPrMonitorError("rejected: remote branch moved")
        return git(args, cwd)
    def gh(args):
        if args == ["api", "repos/acme/gap/pulls/9"]:
            return json.dumps(pull(sha=old_sha))
        if args == ["api", "repos/acme/gap/pulls/10"]:
            return json.dumps(pull(10))
        if args[:2] == ["pr", "diff"]:
            return str(path.relative_to(root))
        pytest.fail(f"No signals are allowed before successful publication: {args}")
    with pytest.raises(monitor.GapPrMonitorError, match="rejected"):
        monitor.retire_previous_leader(
            path, repo_root=root, head_ref="gap-leader/gap-9",
            leader_pr_url=OLD, replacement_url=NEW, check_name="gap",
            dry_run=False, remote="origin", gh_runner=gh, git_runner=conflicting_git,
            updater_factory=lambda **kwargs: pytest.fail("No updater before a successful push"),
        )
    assert not any("pull" in c or "--rebase" in c for c in commands)
    assert git(["rev-parse", "origin/gap-leader/gap-9"], root) == old_sha


def test_merged_replacement_is_valid_but_closed_unmerged_is_not():
    new = pull(10)
    new.update(state="closed", merged=True)
    validate_replacement(OLD, pull(), NEW, new)
    new["merged"] = False
    with pytest.raises(ValueError):
        validate_replacement(OLD, pull(), NEW, new)


def test_latest_rollover_ignores_abandoned_draft_and_nonleader_prs():
    def row(number, state="OPEN", draft=False, branch=None):
        return {
            "number": number, "url": f"https://github.com/acme/gap/pull/{number}",
            "state": state, "isDraft": draft,
            "headRefName": branch or f"gap-leader/gap-{number}",
            "labels": [{"name": "gated-artifacts-promoter"}, {"name": f"gap-{number}"}],
        }
    calls = []
    def gh(args):
        calls.append(args)
        return json.dumps([row(13, branch="feature"), row(12, draft=True),
                           row(11, state="CLOSED"), row(10, state="MERGED"), row(9)])
    newest = monitor.latest_rollover_leader("acme/gap", gh_runner=gh)
    assert newest.number == 10
    assert calls[0][calls[0].index("--state") + 1] == "all"


@pytest.mark.parametrize("enabled", [True, False])
def test_schedule_only_retires_older_leaders_when_enabled(tmp_path, monkeypatch, enabled):
    old = monitor.OpenLeaderPr(9, OLD, "gap-leader/gap-9", ("gated-artifacts-promoter", "gap-9"))
    new = monitor.OpenLeaderPr(10, NEW, "gap-leader/gap-10", ("gated-artifacts-promoter", "gap-10"))
    calls = []
    monkeypatch.setenv("GAP_RETIRE_PREVIOUS_LEADERS", str(enabled).lower())
    monkeypatch.setattr(monitor, "list_open_leader_prs", lambda *a, **k: [new, old])
    monkeypatch.setattr(monitor, "latest_rollover_leader", lambda *a, **k: new)
    monkeypatch.setattr(monitor, "checkout_leader_branch", lambda *a, **k: None)
    monkeypatch.setattr(monitor, "resolve_state_file", lambda **k: tmp_path / "state.json")
    def execute(path, **kwargs):
        calls.append(kwargs)
        return monitor.MonitorResult(path, [], [], [], [], False)
    monkeypatch.setattr(monitor, "run_monitor_publish_and_signal", execute)
    assert monitor.run_scheduled_monitors(tmp_path, leader_repo="acme/gap") == 0
    assert "replacement_url" not in calls[0]
    assert calls[1].get("replacement_url") == (NEW if enabled else None)


def test_creator_rejects_reusing_closed_trigger_before_any_git_changes():
    from lib.leader_pr import LeaderPRManager
    from lib.state_file import build_state
    commands = []
    def run(command, cwd):
        commands.append(command)
        if command[:3] == ["gh", "pr", "list"]:
            result = [{"number": 9, "url": OLD}] if command[command.index("--state") + 1] == "all" else []
            return SimpleNamespace(returncode=0, stdout=json.dumps(result), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    with pytest.raises(RuntimeError, match="closed or merged"):
        LeaderPRManager(repo="acme/gap", runner=run).create_or_update(
            build_state(pull_requests=[]), trigger_id="gap-9",
        )
    assert not any(command[0] == "git" or command[:3] == ["gh", "repo", "clone"] for command in commands)


def test_rollover_dry_run_makes_no_file_or_github_changes(git_leader):
    root, path, git = git_leader
    before = path.read_bytes()
    sha = git(["rev-parse", "HEAD"], root)
    calls = []
    def gh(args):
        calls.append(args)
        if args[:2] == ["pr", "diff"]:
            return str(path.relative_to(root))
        return json.dumps(pull(sha=sha) if args[-1].endswith("/9") else pull(10))
    result = monitor.retire_previous_leader(
        path, repo_root=root, head_ref="gap-leader/gap-9", leader_pr_url=OLD,
        replacement_url=NEW, check_name="gap", dry_run=True, remote="origin",
        gh_runner=gh, git_runner=git,
    )
    assert result.overall_status == "superseded" and result.dry_run
    assert path.read_bytes() == before
    assert not (root / "GAP History").exists()
    assert len(calls) == 3
    assert all((c[0] == "api" and len(c) == 2) or c[:2] == ["pr", "diff"] for c in calls)


def test_unrelated_code_changes_block_history_merge(git_leader):
    root, path, git = git_leader
    before = path.read_bytes()
    sha = git(["rev-parse", "HEAD"], root)
    def gh(args):
        if args[:2] == ["pr", "diff"]:
            return ".github/workflows/unreviewed.yml"
        return json.dumps(pull(sha=sha))
    with pytest.raises(monitor.GapPrMonitorError, match="outside its run record"):
        monitor.retire_previous_leader(
            path, repo_root=root, head_ref="gap-leader/gap-9",
            leader_pr_url=OLD, replacement_url=NEW, check_name="gap",
            dry_run=False, remote="origin", gh_runner=gh, git_runner=git,
        )
    assert path.read_bytes() == before
    assert not (root / "GAP History").exists()


def test_creator_resumes_orphaned_branch_without_force_push(monkeypatch):
    from pathlib import Path
    from lib.leader_pr import LeaderPRManager
    from lib.state_file import build_state
    monkeypatch.setenv("GITHUB_TOKEN", "test-token")
    commands = []
    def run(command, cwd):
        commands.append(command)
        out = ""
        if command[:3] == ["gh", "pr", "list"]:
            out = "[]"
        elif command[:3] == ["gh", "repo", "clone"]:
            Path(command[4]).mkdir(exist_ok=True)
        elif command[:2] == ["git", "ls-remote"]:
            out = SHA + "\\trefs/heads/gap-leader/gap-9"
        elif command[:2] == ["git", "checkout"]:
            state = Path(cwd) / "GAP Leaders/2026-11-05_gap-9/state.json"
            state.parent.mkdir(parents=True)
            state.write_text(json.dumps(payload()))
        elif command[:2] == ["git", "status"]:
            out = "M state.json"
        elif command[:3] == ["gh", "pr", "create"]:
            out = OLD
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    manager = LeaderPRManager(repo="acme/gap", runner=run)
    state = build_state(pull_requests=[{"repo": "component", "pr-url": "https://github.com/acme/component/pull/3"}])
    result = manager.create_or_update(state, trigger_id="gap-9")
    assert result.pr_url == OLD
    assert result.state_path == "GAP Leaders/2026-11-05_gap-9/state.json"
    assert any(c[:2] == ["git", "fetch"] for c in commands)
    assert state.pull_requests[0].build_observations["head-sha"] == "b" * 40
    assert not any("--force" in c for c in commands if c[0] == "git")
