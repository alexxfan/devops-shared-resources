from __future__ import annotations

import pytest

from lib.config_parser import (
    ConfigError,
    filter_sync_entries,
    normalize_sync_entry,
    parse_sync_config,
)


def test_parse_single_entry_defaults_dest_url() -> None:
    entries = parse_sync_config(
        {
            "src": {"url": "https://github.com/org/repo.git", "branch": "main"},
            "dest": {"branch": "stable"},
        }
    )
    assert len(entries) == 1
    entry = entries[0]
    assert entry["sync_type"] == "pr"
    assert entry["dest"]["url"] == "https://github.com/org/repo.git"
    assert entry["ignore_files"] == []


def test_parse_ignore_files_from_string_and_list() -> None:
    string_entry = normalize_sync_entry(
        {
            "src": {"url": "https://github.com/org/a.git", "branch": "main"},
            "dest": {"branch": "stable"},
            "ignore-files": "README.md config/overrides.yaml",
        }
    )
    list_entry = normalize_sync_entry(
        {
            "src": {"url": "https://github.com/org/a.git", "branch": "main"},
            "dest": {"branch": "stable"},
            "ignore-files": ["README.md", "config/overrides.yaml"],
        }
    )
    assert string_entry["ignore_files"] == ["README.md", "config/overrides.yaml"]
    assert list_entry["ignore_files"] == ["README.md", "config/overrides.yaml"]


def test_parse_syncs_wrapper() -> None:
    entries = parse_sync_config(
        {
            "syncs": [
                {
                    "name": "main-to-stable",
                    "sync-type": "commit-merge",
                    "src": {"url": "https://github.com/org/repo.git", "branch": "main"},
                    "dest": {"branch": "stable"},
                }
            ]
        }
    )
    assert entries[0]["name"] == "main-to-stable"
    assert entries[0]["sync_type"] == "commit-merge"


def test_missing_required_fields_raise() -> None:
    with pytest.raises(ConfigError, match="src.url is required"):
        normalize_sync_entry({"src": {"branch": "main"}, "dest": {"branch": "stable"}})

    with pytest.raises(ConfigError, match="dest.branch is required"):
        normalize_sync_entry(
            {"src": {"url": "https://github.com/org/repo.git", "branch": "main"}, "dest": {}}
        )


def test_parse_ignore_files_from_comma_separated_string() -> None:
    entry = normalize_sync_entry(
        {
            "src": {"url": "https://github.com/org/a.git", "branch": "main"},
            "dest": {"branch": "stable"},
            "ignore-files": ".tekton/*, build/**, release/**",
        }
    )
    assert entry["ignore_files"] == [".tekton/*", "build/**", "release/**"]


def test_parse_git_infra_format() -> None:
    entries = parse_sync_config(
        {
            "defaults": {
                "source-branch": "main",
                "target-branch": "stable",
                "tracking-label": "lake-gate",
            },
            "git": [
                {
                    "name": "kserve",
                    "automerge": "yes",
                    "repo-url": "https://github.com/red-hat-data-services/kserve.git",
                    "ignore-files": ".tekton/*",
                },
                {
                    "name": "rhoai-additional-images",
                    "automerge": "no",
                    "repo-url": "https://github.com/red-hat-data-services/rhoai-additional-images.git",
                },
            ],
        }
    )

    assert len(entries) == 2
    assert entries[0]["name"] == "kserve"
    assert entries[0]["src"]["url"] == "https://github.com/red-hat-data-services/kserve.git"
    assert entries[0]["src"]["branch"] == "main"
    assert entries[0]["dest"]["branch"] == "stable"
    assert entries[0]["ignore_files"] == [".tekton/*"]
    assert entries[0]["pr"]["tracking_label"] == "lake-gate"
    assert entries[0]["pr"]["automerge"] is True
    assert entries[1]["pr"]["automerge"] is False


def test_filter_sync_entries_by_name() -> None:
    entries = parse_sync_config(
        {
            "git": [
                {
                    "name": "kserve",
                    "repo-url": "https://github.com/red-hat-data-services/kserve.git",
                },
                {
                    "name": "kubeflow",
                    "repo-url": "https://github.com/red-hat-data-services/kubeflow.git",
                },
            ]
        }
    )

    filtered = filter_sync_entries(entries, "kserve,kubeflow")
    assert [entry["name"] for entry in filtered] == ["kserve", "kubeflow"]

    single = filter_sync_entries(entries, "kserve")
    assert [entry["name"] for entry in single] == ["kserve"]


def test_filter_sync_entries_unknown_name_raises() -> None:
    entries = parse_sync_config(
        {
            "git": [
                {
                    "name": "kserve",
                    "repo-url": "https://github.com/red-hat-data-services/kserve.git",
                }
            ]
        }
    )

    with pytest.raises(ConfigError, match="Unknown sync name"):
        filter_sync_entries(entries, "missing")


def test_invalid_sync_type_raises() -> None:
    with pytest.raises(ConfigError, match="sync-type must be one of"):
        normalize_sync_entry(
            {
                "sync-type": "invalid",
                "src": {"url": "https://github.com/org/repo.git", "branch": "main"},
                "dest": {"branch": "stable"},
            }
        )
