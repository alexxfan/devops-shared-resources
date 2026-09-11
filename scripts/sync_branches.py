#!/usr/bin/env python3
"""Sync one git branch into another via PR, direct push, or merge commit."""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lib.config_parser import (
    ConfigError,
    filter_sync_entries,
    load_sync_config,
    normalize_sync_entry,
)
from lib.git_utils import (
    add_remote,
    checkout_remote_branch,
    clone_repo,
    configure_identity,
    fetch_remote,
    is_local_path,
    list_commits_between,
    normalize_repo_url,
    resolve_branch_ref,
    push_branch,
    run_git,
)
from lib.merge_resolver import MergeError, merge_branches
from lib.pr_creator import (
    PRCreator,
    format_default_pr_body,
    format_default_pr_title,
)


@dataclass
class SyncOutcome:
    sync_type: str
    message: str
    pr_url: str | None = None
    branch: str | None = None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync a source branch into a target branch.",
    )
    parser.add_argument(
        "--config",
        help="YAML config file with one or more sync entries. "
        "When set, per-entry CLI flags below must not be used.",
    )
    parser.add_argument(
        "--only",
        help="Comma-separated sync entry names to run. Requires --config.",
    )
    parser.add_argument(
        "--sync-type",
        choices=["pr", "push", "commit-merge"],
        default=None,
        help="Sync mode: open a PR (default), push a bootstrap branch, or merge directly.",
    )
    parser.add_argument("--source-repo", default=None, help="Source repository URL or local path.")
    parser.add_argument("--source-branch", default=None, help="Source branch to merge from.")
    parser.add_argument(
        "--target-repo",
        default=None,
        help="Target repository URL or local path. Defaults to the source repo.",
    )
    parser.add_argument("--target-branch", default=None, help="Target branch to merge into.")
    parser.add_argument(
        "--ignore-files",
        nargs="*",
        default=None,
        help="Files or glob patterns to keep at the target version on conflict.",
    )
    parser.add_argument("--token", help="GitHub token for clone/push/PR operations.")
    parser.add_argument("--dry-run", action="store_true", help="Plan actions without changing remotes.")
    parser.add_argument("--pr-branch", help="Branch name to use when opening a PR.")
    parser.add_argument("--tracking-label", help="Reuse an open PR with this label instead of creating a new one.")
    parser.add_argument("--label", dest="labels", action="append", default=[], help="Additional PR label.")
    parser.add_argument("--automerge", action="store_true", help="Enable GitHub automerge on created PRs.")
    parser.add_argument("--pr-title", help="Custom PR title.")
    parser.add_argument("--pr-body", help="Custom PR body.")
    parser.add_argument("--reviewer", dest="reviewers", action="append", default=[], help="PR reviewer to request.")
    return parser


def _validate_cli_exclusivity(args: argparse.Namespace) -> None:
    if args.only and not args.config:
        raise ConfigError("--only requires --config")
    if not args.config:
        return
    conflicting = []
    flag_names = {
        "sync_type": "--sync-type",
        "source_repo": "--source-repo",
        "source_branch": "--source-branch",
        "target_repo": "--target-repo",
        "target_branch": "--target-branch",
        "ignore_files": "--ignore-files",
    }
    for name, flag in flag_names.items():
        if getattr(args, name) is not None:
            conflicting.append(flag)
    if conflicting:
        raise ConfigError(
            "When --config is provided, do not also pass: " + ", ".join(conflicting)
        )


def _entry_from_args(args: argparse.Namespace) -> dict[str, Any]:
    if not args.source_repo or not args.source_branch or not args.target_branch:
        raise ConfigError(
            "Without --config, --source-repo, --source-branch, and --target-branch are required."
        )
    return normalize_sync_entry(
        {
            "sync-type": args.sync_type or "pr",
            "src": {"url": args.source_repo, "branch": args.source_branch},
            "dest": {
                "url": args.target_repo or args.source_repo,
                "branch": args.target_branch,
            },
            "ignore-files": args.ignore_files or [],
            "pr": {
                "branch": args.pr_branch,
                "tracking-label": args.tracking_label,
                "labels": args.labels,
                "automerge": args.automerge,
                "title": args.pr_title,
                "body": args.pr_body,
                "reviewers": args.reviewers,
            },
        }
    )


def _default_pr_branch(entry: dict[str, Any]) -> str:
    configured = entry["pr"]["branch"]
    if configured:
        return configured
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"sync-{entry['src']['branch']}-to-{entry['dest']['branch']}-{timestamp}"


def _prepare_worktree(
    entry: dict[str, Any],
    *,
    token: str | None,
    work_branch: str,
) -> tuple[Path, str]:
    """Clone the target repo and return (repo_path, source_ref)."""
    target_url = normalize_repo_url(entry["dest"]["url"])
    source_url = normalize_repo_url(entry["src"]["url"])
    workdir = Path(tempfile.mkdtemp(prefix="sync-branches-"))
    clone_repo(target_url, workdir, token=token)
    configure_identity(workdir)

    tracking_branch = None
    if entry["sync_type"] == "pr" and entry["pr"]["tracking_label"] and token:
        creator = PRCreator(token)
        existing = creator.find_open_pr_by_label(
            target_url,
            base_branch=entry["dest"]["branch"],
            label=entry["pr"]["tracking_label"],
        )
        if existing:
            tracking_branch = existing["head"]["ref"]

    checkout_remote_branch(workdir, entry["dest"]["branch"])
    branch_to_use = tracking_branch or work_branch
    run_git(["checkout", "-B", branch_to_use], cwd=workdir)

    if source_url != target_url or not is_local_path(source_url):
        add_remote(workdir, "source", source_url, token=token)
        fetch_remote(workdir, "source", tags=True)
        source_ref = f"source/{entry['src']['branch']}"
    else:
        fetch_remote(workdir, "origin", refspec=entry["src"]["branch"])
        source_ref = f"origin/{entry['src']['branch']}"

    return workdir, source_ref


def run_sync_entry(
    entry: dict[str, Any],
    *,
    token: str | None,
    dry_run: bool = False,
) -> SyncOutcome:
    sync_type = entry["sync_type"]
    source = entry["src"]
    target = entry["dest"]
    pr_branch = _default_pr_branch(entry)

    if dry_run:
        return SyncOutcome(
            sync_type=sync_type,
            message=(
                f"Dry run: would sync {source['url']}:{source['branch']} "
                f"-> {target['url']}:{target['branch']} using {sync_type}"
            ),
            branch=pr_branch if sync_type == "pr" else target["branch"],
        )

    workdir: Path | None = None
    try:
        if sync_type == "push":
            workdir = Path(tempfile.mkdtemp(prefix="sync-branches-push-"))
            clone_repo(source["url"], workdir, token=token, branch=source["branch"])
            configure_identity(workdir)
            run_git(["checkout", "-B", target["branch"]], cwd=workdir)
            push_branch(workdir, target["branch"], force=True)
            return SyncOutcome(
                sync_type=sync_type,
                message=f"Pushed bootstrap branch {target['branch']}",
                branch=target["branch"],
            )

        work_branch = target["branch"] if sync_type == "commit-merge" else pr_branch
        workdir, source_ref = _prepare_worktree(entry, token=token, work_branch=work_branch)
        target_ref = resolve_branch_ref(workdir, target["branch"])
        commits_to_sync = list_commits_between(workdir, target_ref, source_ref)

        merge_result = merge_branches(
            workdir,
            source_ref=source_ref,
            ignore_files=entry["ignore_files"],
            merge_args=entry["merge_args"],
            commit_message=f"Sync {source['branch']} into {target['branch']}",
        )

        if merge_result.already_up_to_date:
            return SyncOutcome(
                sync_type=sync_type,
                message="Target branch is already up to date.",
                branch=work_branch,
            )

        push_force = sync_type == "pr" and entry["pr"]["tracking_label"] is not None
        push_branch(workdir, work_branch, force=push_force)

        if sync_type == "commit-merge":
            return SyncOutcome(
                sync_type=sync_type,
                message="Merged and pushed directly to target branch.",
                branch=target["branch"],
            )

        if not token:
            raise ConfigError("A GitHub token is required to create or update pull requests.")

        creator = PRCreator(token)
        title = entry["pr"]["title"] or format_default_pr_title(
            source["branch"],
            target["branch"],
            source_repo=source["url"],
            target_repo=target["url"],
        )
        body = entry["pr"]["body"] or format_default_pr_body(
            source["branch"],
            target["branch"],
            source_repo=source["url"],
            target_repo=target["url"],
            commits=commits_to_sync,
            head_branch=work_branch if sync_type == "pr" else None,
            automerge=entry["pr"]["automerge"],
        )
        pr_result = creator.create_or_update_tracking_pr(
            target["url"],
            title=title,
            body=body,
            head_branch=work_branch,
            base_branch=target["branch"],
            tracking_label=entry["pr"]["tracking_label"],
            labels=entry["pr"]["labels"],
            reviewers=entry["pr"]["reviewers"],
            automerge=entry["pr"]["automerge"],
        )
        action = "Updated" if pr_result.updated else "Created"
        return SyncOutcome(
            sync_type=sync_type,
            message=f"{action} pull request #{pr_result.number}",
            pr_url=pr_result.url,
            branch=pr_result.branch,
        )
    finally:
        if workdir and workdir.exists():
            shutil.rmtree(workdir, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        _validate_cli_exclusivity(args)
        if args.config:
            entries = filter_sync_entries(load_sync_config(args.config), args.only)
        else:
            entries = [_entry_from_args(args)]

        outcomes: list[SyncOutcome] = []
        for entry in entries:
            outcome = run_sync_entry(entry, token=args.token, dry_run=args.dry_run)
            outcomes.append(outcome)
            print(outcome.message)
            if outcome.pr_url:
                print(outcome.pr_url)

        return 0
    except (ConfigError, MergeError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
