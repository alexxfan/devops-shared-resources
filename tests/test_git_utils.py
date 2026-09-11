from __future__ import annotations

from pathlib import Path

from tests.conftest import run

from lib.git_utils import GitCommit, list_commits_between


def test_list_commits_between(git_repo_factory) -> None:
    repo = git_repo_factory("repo")
    readme = repo / "README.md"
    readme.write_text("main\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repo)
    run(["git", "commit", "-m", "main init"], cwd=repo)
    run(["git", "branch", "stable"], cwd=repo)
    readme.write_text("main v2\n", encoding="utf-8")
    run(["git", "add", "README.md"], cwd=repo)
    run(["git", "commit", "-m", "main update"], cwd=repo)

    commits = list_commits_between(repo, "stable", "main")

    assert commits == [
        GitCommit(
            sha=run(["git", "rev-parse", "main"], cwd=repo).stdout.strip(),
            short_sha=run(["git", "rev-parse", "--short", "main"], cwd=repo).stdout.strip(),
            subject="main update",
        )
    ]
