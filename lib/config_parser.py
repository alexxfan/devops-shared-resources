"""Parse and validate branch-sync configuration files."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

SYNC_TYPES = {"pr", "push", "commit-merge"}
PR_HEAD_STRATEGIES = {"sync-branch", "source"}
REQUIRED_SRC_FIELDS = ("url", "branch")
REQUIRED_DEST_FIELDS = ("branch",)


class ConfigError(ValueError):
    """Raised when a sync configuration file is invalid."""


def _ensure_mapping(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{field_name} must be a mapping")
    return value


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"yes", "true", "1", "y"}
    return bool(value)


def _split_listish_string(value: str) -> list[str]:
    return [item.strip() for item in value.replace(",", " ").split() if item.strip()]


def _normalize_ignore_files(value: Any, entry_index: int) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _split_listish_string(value)
    if isinstance(value, list):
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not item.strip():
                raise ConfigError(
                    f"sync entry {entry_index}: ignore-files entries must be non-empty strings"
                )
            normalized.append(item.strip())
        return normalized
    raise ConfigError(
        f"sync entry {entry_index}: ignore-files must be a string or list of strings"
    )


def _normalize_sync_type(value: Any, entry_index: int) -> str:
    sync_type = (value or "pr").strip().lower() if isinstance(value, str) else "pr"
    if sync_type not in SYNC_TYPES:
        raise ConfigError(
            f"sync entry {entry_index}: sync-type must be one of {sorted(SYNC_TYPES)}"
        )
    return sync_type


def _normalize_pr_head_strategy(value: Any, entry_index: int) -> str:
    strategy = (value or "sync-branch").strip().lower() if isinstance(value, str) else "sync-branch"
    if strategy not in PR_HEAD_STRATEGIES:
        raise ConfigError(
            f"sync entry {entry_index}: pr head strategy must be one of {sorted(PR_HEAD_STRATEGIES)}"
        )
    return strategy


def _normalize_pr_section(value: Any, entry_index: int) -> dict[str, Any]:
    if value is None:
        return {
            "branch": None,
            "head_strategy": "sync-branch",
            "tracking_label": None,
            "labels": [],
            "automerge": False,
            "title": None,
            "body": None,
            "reviewers": [],
        }
    pr = _ensure_mapping(value, f"sync entry {entry_index} pr")
    labels = pr.get("labels") or []
    if isinstance(labels, str):
        labels = [item for item in labels.split() if item]
    elif not isinstance(labels, list):
        raise ConfigError(f"sync entry {entry_index}: pr.labels must be a list or string")

    reviewers = pr.get("reviewers") or []
    if isinstance(reviewers, str):
        reviewers = [item.strip() for item in reviewers.replace(",", " ").split() if item.strip()]
    elif not isinstance(reviewers, list):
        raise ConfigError(f"sync entry {entry_index}: pr.reviewers must be a list or string")

    return {
        "branch": pr.get("branch"),
        "head_strategy": _normalize_pr_head_strategy(
            pr.get("head-strategy")
            or pr.get("head_strategy")
            or pr.get("pr-head")
            or pr.get("pr_head"),
            entry_index,
        ),
        "tracking_label": pr.get("tracking-label") or pr.get("tracking_label"),
        "labels": [str(label) for label in labels],
        "automerge": _parse_bool(pr.get("automerge"), default=False),
        "title": pr.get("title"),
        "body": pr.get("body"),
        "reviewers": [str(reviewer) for reviewer in reviewers],
    }


def normalize_sync_entry(raw_entry: dict[str, Any], entry_index: int = 0) -> dict[str, Any]:
    """Normalize a single sync configuration entry."""
    entry = _ensure_mapping(raw_entry, f"sync entry {entry_index}")

    src = _ensure_mapping(entry.get("src"), f"sync entry {entry_index} src")
    for field in REQUIRED_SRC_FIELDS:
        if not src.get(field):
            raise ConfigError(f"sync entry {entry_index}: src.{field} is required")

    dest = _ensure_mapping(entry.get("dest") or {}, f"sync entry {entry_index} dest")
    for field in REQUIRED_DEST_FIELDS:
        if not dest.get(field):
            raise ConfigError(f"sync entry {entry_index}: dest.{field} is required")

    dest_url = dest.get("url") or src["url"]
    if not dest_url:
        raise ConfigError(f"sync entry {entry_index}: dest.url could not be determined")

    return {
        "name": entry.get("name"),
        "sync_type": _normalize_sync_type(entry.get("sync-type") or entry.get("sync_type"), entry_index),
        "src": {
            "url": str(src["url"]).strip(),
            "branch": str(src["branch"]).strip(),
        },
        "dest": {
            "url": str(dest_url).strip(),
            "branch": str(dest["branch"]).strip(),
        },
        "ignore_files": _normalize_ignore_files(
            entry.get("ignore-files") or entry.get("ignore_files"),
            entry_index,
        ),
        "pr": _normalize_pr_section(entry.get("pr"), entry_index),
        "merge_args": entry.get("merge-args") or entry.get("merge_args") or [],
        "fetch_args": entry.get("fetch-args") or entry.get("fetch_args") or [],
        "push_args": entry.get("push-args") or entry.get("push_args") or [],
    }


def _normalize_defaults(value: Any) -> dict[str, Any]:
    defaults = _ensure_mapping(value, "defaults") if value is not None else {}
    return {
        "sync_type": defaults.get("sync-type") or defaults.get("sync_type") or "pr",
        "source_branch": defaults.get("source-branch") or defaults.get("source_branch") or "main",
        "target_branch": defaults.get("target-branch") or defaults.get("target_branch") or "stable",
        "tracking_label": defaults.get("tracking-label") or defaults.get("tracking_label") or "lake-gate",
        "automerge": _parse_bool(defaults.get("automerge"), default=False),
        "pr_head_strategy": _normalize_pr_head_strategy(
            defaults.get("pr-head") or defaults.get("pr_head") or defaults.get("head-strategy"),
            0,
        ),
    }


def _git_entry_to_sync_entry(
    raw_entry: dict[str, Any],
    *,
    defaults: dict[str, Any],
    entry_index: int,
) -> dict[str, Any]:
    """Convert an infra-repo `git:` list entry into a normalized sync entry shape."""
    entry = _ensure_mapping(raw_entry, f"git entry {entry_index}")
    name = entry.get("name")
    if not name:
        raise ConfigError(f"git entry {entry_index}: name is required")

    repo_url = entry.get("repo-url") or entry.get("repo_url") or entry.get("url")
    if not repo_url:
        raise ConfigError(f"git entry {entry_index}: repo-url is required")

    source_branch = (
        entry.get("source-branch")
        or entry.get("source_branch")
        or defaults["source_branch"]
    )
    target_branch = (
        entry.get("target-branch")
        or entry.get("target_branch")
        or defaults["target_branch"]
    )
    tracking_label = (
        entry.get("tracking-label")
        or entry.get("tracking_label")
        or defaults["tracking_label"]
    )
    automerge = entry.get("automerge")
    if automerge is None:
        automerge = defaults["automerge"]

    return {
        "name": str(name).strip(),
        "sync-type": entry.get("sync-type") or entry.get("sync_type") or defaults["sync_type"],
        "src": {
            "url": str(repo_url).strip(),
            "branch": str(source_branch).strip(),
        },
        "dest": {
            "branch": str(target_branch).strip(),
        },
        "ignore-files": entry.get("ignore-files") or entry.get("ignore_files"),
        "pr": {
            "tracking-label": tracking_label,
            "automerge": automerge,
            "head-strategy": (
                entry.get("pr-head")
                or entry.get("pr_head")
                or entry.get("head-strategy")
                or entry.get("head_strategy")
                or defaults["pr_head_strategy"]
            ),
        },
    }


def filter_sync_entries(
    entries: list[dict[str, Any]],
    only: str | None,
) -> list[dict[str, Any]]:
    """Return entries whose name matches one of the comma-separated values in only."""
    if not only:
        return entries

    requested = {name.strip() for name in only.split(",") if name.strip()}
    if not requested:
        raise ConfigError("--only must include at least one sync name")

    available = {entry.get("name") for entry in entries if entry.get("name")}
    unknown = requested - available
    if unknown:
        raise ConfigError(
            "Unknown sync name(s): " + ", ".join(sorted(unknown))
        )

    filtered = [entry for entry in entries if entry.get("name") in requested]
    if not filtered:
        raise ConfigError("No sync entries matched --only filter")
    return filtered


def parse_sync_config(data: Any) -> list[dict[str, Any]]:
    """Parse raw YAML/JSON data into normalized sync entries."""
    if data is None:
        raise ConfigError("configuration is empty")

    if isinstance(data, dict) and "git" in data:
        git_entries = data["git"]
        if not isinstance(git_entries, list) or not git_entries:
            raise ConfigError("git must be a non-empty list")
        defaults = _normalize_defaults(data.get("defaults"))
        entries = [
            _git_entry_to_sync_entry(entry, defaults=defaults, entry_index=index)
            for index, entry in enumerate(git_entries)
        ]
    elif isinstance(data, dict) and "syncs" in data:
        entries = data["syncs"]
    elif isinstance(data, list):
        entries = data
    elif isinstance(data, dict):
        entries = [data]
    else:
        raise ConfigError("configuration must be a mapping or list of mappings")

    if not isinstance(entries, list) or not entries:
        raise ConfigError("configuration must contain at least one sync entry")

    return [normalize_sync_entry(entry, index) for index, entry in enumerate(entries)]


def load_sync_config(path: str | Path) -> list[dict[str, Any]]:
    """Load and normalize a sync configuration file."""
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(f"configuration file not found: {config_path}")

    with config_path.open(encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    return parse_sync_config(data)
