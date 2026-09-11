"""Merge one git branch into another with ignore-file resolution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from lib.git_utils import paths_matching_patterns, run_git


@dataclass(frozen=True)
class MergeResult:
    """Outcome of a merge attempt."""

    merged: bool
    already_up_to_date: bool
    message: str


class MergeError(RuntimeError):
    """Raised when a merge cannot be completed."""


def _configure_ignore_attributes(repo_path: Path, ignore_files: Sequence[str]) -> None:
    if not ignore_files:
        return
    attributes_path = repo_path / ".git" / "info" / "attributes"
    attributes_path.parent.mkdir(parents=True, exist_ok=True)
    existing = attributes_path.read_text(encoding="utf-8") if attributes_path.exists() else ""
    lines = [line for line in existing.splitlines() if line.strip()]
    for pattern in ignore_files:
        attribute_line = f"{pattern} merge=ours"
        if attribute_line not in lines:
            lines.append(attribute_line)
    attributes_path.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    run_git(["config", "merge.ours.driver", "true"], cwd=repo_path)


def _restore_excluded_files(
    repo_path: Path,
    downstream_head: str,
    ignore_files: Sequence[str],
) -> bool:
    if not ignore_files:
        return False

    diff = run_git(
        ["diff", "--name-status", downstream_head, "HEAD"],
        cwd=repo_path,
    ).stdout.splitlines()

    for line in diff:
        if not line.strip():
            continue
        parts = line.split("\t", 1)
        if len(parts) != 2:
            continue
        status, file_path = parts[0], parts[1]
        if file_path not in paths_matching_patterns([file_path], ignore_files):
            continue

        if status == "A":
            run_git(["rm", "-f", file_path], cwd=repo_path)
            continue

        target = run_git(["show", f"{downstream_head}:{file_path}"], cwd=repo_path, check=False)
        if target.returncode != 0:
            continue

        working_path = repo_path / file_path
        if working_path.exists() and working_path.read_text(encoding="utf-8") == target.stdout:
            continue

        run_git(["checkout", downstream_head, "--", file_path], cwd=repo_path)

    staged = run_git(["diff", "--cached", "--name-only"], cwd=repo_path).stdout.strip()
    if not staged:
        return False

    parents = run_git(["rev-list", "--parents", "-n", "1", "HEAD"], cwd=repo_path).stdout.split()
    is_merge_commit = len(parents) > 2
    if is_merge_commit:
        run_git(["commit", "--no-verify", "--amend", "--no-edit"], cwd=repo_path)
    else:
        run_git(
            ["commit", "--no-verify", "-m", "Restore ignored files from target branch"],
            cwd=repo_path,
        )
    return True


def _resolve_conflicts(repo_path: Path, ignore_files: Sequence[str]) -> None:
    unmerged = run_git(
        ["diff", "--name-only", "--diff-filter=U"],
        cwd=repo_path,
    ).stdout.splitlines()
    unmerged = [line for line in unmerged if line.strip()]
    if not unmerged:
        return

    unresolved: list[str] = []
    for file_path in unmerged:
        if file_path in paths_matching_patterns([file_path], ignore_files):
            ours_exists = (
                run_git(["show", f":2:{file_path}"], cwd=repo_path, check=False).returncode == 0
            )
            if ours_exists:
                run_git(["checkout", "--ours", file_path], cwd=repo_path)
                run_git(["add", file_path], cwd=repo_path)
            else:
                run_git(["rm", "-f", file_path], cwd=repo_path)
        else:
            unresolved.append(file_path)

    if unresolved:
        run_git(["merge", "--abort"], cwd=repo_path, check=False)
        raise MergeError(
            "Unresolvable merge conflicts remain on: " + ", ".join(unresolved)
        )


def merge_branches(
    repo_path: str | Path,
    *,
    source_ref: str,
    target_branch: str | None = None,
    ignore_files: Sequence[str] = (),
    merge_args: Sequence[str] = (),
    commit_message: str = "Merged upstream",
    dry_run: bool = False,
) -> MergeResult:
    """
    Merge ``source_ref`` into the current branch of ``repo_path``.

    The current branch is treated as the downstream/target branch. Files matching
    ``ignore_files`` keep the target version when conflicts occur.
    """
    repo = Path(repo_path)
    if target_branch:
        run_git(["checkout", target_branch], cwd=repo)

    downstream_head = run_git(["rev-parse", "HEAD"], cwd=repo).stdout.strip()
    _configure_ignore_attributes(repo, ignore_files)

    effective_merge_args = list(merge_args)
    if ignore_files and "--no-ff" not in effective_merge_args:
        effective_merge_args.insert(0, "--no-ff")
    merge_command = ["merge", *effective_merge_args, source_ref]
    if dry_run:
        return MergeResult(
            merged=False,
            already_up_to_date=False,
            message=f"Dry run: would execute git {' '.join(merge_command)}",
        )

    merge_result = run_git(merge_command, cwd=repo, check=False)
    output = ((merge_result.stdout or "") + (merge_result.stderr or "")).strip()

    if merge_result.returncode != 0:
        if "CONFLICT" in output:
            if not ignore_files:
                run_git(["merge", "--abort"], cwd=repo, check=False)
                raise MergeError("Merge conflicts detected and no ignore patterns were provided")
            _resolve_conflicts(repo, ignore_files)
            run_git(["commit", "--no-verify", "--no-edit", "-m", commit_message], cwd=repo)
            _restore_excluded_files(repo, downstream_head, ignore_files)
            return MergeResult(merged=True, already_up_to_date=False, message=output)

        run_git(["merge", "--abort"], cwd=repo, check=False)
        raise MergeError(output or "Merge failed")

    if "Already up to date." in output:
        return MergeResult(merged=False, already_up_to_date=True, message=output)

    _restore_excluded_files(repo, downstream_head, ignore_files)
    return MergeResult(merged=True, already_up_to_date=False, message=output)
