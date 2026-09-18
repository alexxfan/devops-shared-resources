from __future__ import annotations

from pathlib import Path

import pytest

from lib.merge_resolver import MergeError, merge_branches
from tests.conftest import run


def _commit(repo: Path, relative_path: str, content: str, message: str) -> None:
    file_path = repo / relative_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    run(["git", "add", relative_path], cwd=repo)
    run(["git", "commit", "-m", message], cwd=repo)


def test_merge_applies_source_changes(git_repo_factory) -> None:
    repo = git_repo_factory("merge-target")
    _commit(repo, "README.md", "target\n", "target init")
    run(["git", "branch", "stable"], cwd=repo)
    _commit(repo, "README.md", "main\n", "main change")
    run(["git", "checkout", "stable"], cwd=repo)

    result = merge_branches(repo, source_ref="main")
    assert result.merged is True
    assert (repo / "README.md").read_text(encoding="utf-8") == "main\n"


def test_merge_keeps_ignored_files_from_target(git_repo_factory) -> None:
    repo = git_repo_factory("merge-ignore")
    _commit(repo, "README.md", "target readme\n", "target init")
    _commit(repo, "config.yaml", "target config\n", "target config")
    run(["git", "branch", "stable"], cwd=repo)
    _commit(repo, "README.md", "main readme\n", "main readme")
    _commit(repo, "config.yaml", "main config\n", "main config")
    run(["git", "checkout", "stable"], cwd=repo)

    result = merge_branches(
        repo,
        source_ref="main",
        ignore_files=["config.yaml"],
    )
    assert result.merged is True
    assert (repo / "README.md").read_text(encoding="utf-8") == "main readme\n"
    assert (repo / "config.yaml").read_text(encoding="utf-8") == "target config\n"


def test_merge_conflict_with_allow_conflicts_commits(git_repo_factory) -> None:
    repo = git_repo_factory("merge-conflict-pr")
    _commit(repo, "README.md", "target\n", "target init")
    run(["git", "branch", "stable"], cwd=repo)
    _commit(repo, "README.md", "main\n", "main change")
    run(["git", "checkout", "stable"], cwd=repo)
    _commit(repo, "README.md", "stable\n", "stable change")

    result = merge_branches(repo, source_ref="main", allow_conflicts=True)
    assert result.merged is True
    assert result.conflict_files == ("README.md",)
    assert "<<<<<<<" in (repo / "README.md").read_text(encoding="utf-8")


def test_merge_conflict_without_ignore_raises(git_repo_factory) -> None:
    repo = git_repo_factory("merge-conflict")
    _commit(repo, "README.md", "target\n", "target init")
    run(["git", "branch", "stable"], cwd=repo)
    _commit(repo, "README.md", "main\n", "main change")
    run(["git", "checkout", "stable"], cwd=repo)
    _commit(repo, "README.md", "stable\n", "stable change")

    with pytest.raises(MergeError, match="no ignore patterns"):
        merge_branches(repo, source_ref="main")


def test_merge_already_up_to_date(git_repo_factory) -> None:
    repo = git_repo_factory("merge-uptodate")
    _commit(repo, "README.md", "same\n", "init")
    run(["git", "branch", "stable"], cwd=repo)
    run(["git", "checkout", "stable"], cwd=repo)

    result = merge_branches(repo, source_ref="main")
    assert result.already_up_to_date is True
    assert result.merged is False
