from __future__ import annotations

from pathlib import Path

from lib.merge_resolver import merge_branches
from scripts.sync_branches import run_sync_entry
from tests.conftest import run


def _commit(repo: Path, relative_path: str, content: str, message: str) -> None:
    file_path = repo / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    run(["git", "add", relative_path], cwd=repo)
    run(["git", "commit", "-m", message], cwd=repo)


def test_local_commit_merge_sync(git_repo_factory, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    run(["git", "init", "--bare", str(origin)], cwd=tmp_path)

    repo = git_repo_factory("local-sync")
    run(["git", "remote", "add", "origin", str(origin)], cwd=repo)
    _commit(repo, "README.md", "stable\n", "stable init")
    _commit(repo, "keep.txt", "stable value\n", "stable keep")
    run(["git", "push", "-u", "origin", "main"], cwd=repo)
    run(["git", "branch", "stable"], cwd=repo)
    run(["git", "push", "-u", "origin", "stable"], cwd=repo)
    _commit(repo, "README.md", "main\n", "main readme")
    _commit(repo, "keep.txt", "main value\n", "main keep")
    run(["git", "push", "origin", "main"], cwd=repo)

    entry = {
        "sync_type": "commit-merge",
        "src": {"url": str(origin), "branch": "main"},
        "dest": {"url": str(origin), "branch": "stable"},
        "ignore_files": ["keep.txt"],
        "pr": {
            "branch": None,
            "tracking_label": None,
            "labels": [],
            "automerge": False,
            "title": None,
            "body": None,
            "reviewers": [],
        },
        "merge_args": [],
        "fetch_args": [],
        "push_args": [],
    }

    outcome = run_sync_entry(entry, token=None, dry_run=False)
    assert "Merged and pushed" in outcome.message or "up to date" in outcome.message

    verify = tmp_path / "verify"
    run(["git", "clone", str(origin), str(verify)], cwd=tmp_path)
    run(["git", "checkout", "stable"], cwd=verify)
    assert (verify / "README.md").read_text(encoding="utf-8") == "main\n"
    assert (verify / "keep.txt").read_text(encoding="utf-8") == "stable value\n"


def test_merge_resolver_end_to_end_with_bare_remote(git_repo_factory, tmp_path: Path) -> None:
    origin = tmp_path / "origin.git"
    run(["git", "init", "--bare", str(origin)], cwd=tmp_path)

    downstream = git_repo_factory("downstream")
    _commit(downstream, "README.md", "stable\n", "stable init")
    run(["git", "remote", "add", "origin", str(origin)], cwd=downstream)
    run(["git", "push", "-u", "origin", "main"], cwd=downstream)
    run(["git", "branch", "stable"], cwd=downstream)
    run(["git", "push", "-u", "origin", "stable"], cwd=downstream)

    upstream = git_repo_factory("upstream")
    run(["git", "clone", str(origin), str(tmp_path / "upstream-clone")], cwd=tmp_path)
    upstream = tmp_path / "upstream-clone"
    _commit(upstream, "README.md", "main\n", "main change")
    run(["git", "push", "origin", "main"], cwd=upstream)

    run(["git", "clone", str(origin), str(tmp_path / "work")], cwd=tmp_path)
    work = tmp_path / "work"
    run(["git", "checkout", "stable"], cwd=work)
    run(["git", "remote", "add", "source", str(origin)], cwd=work)
    run(["git", "fetch", "source", "main"], cwd=work)

    result = merge_branches(work, source_ref="source/main")
    assert result.merged is True
    assert (work / "README.md").read_text(encoding="utf-8") == "main\n"
