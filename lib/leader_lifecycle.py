"""Retire replaced Leaders without rewriting their build or test results."""

from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any

KEY = "leader-lifecycle"
OUTCOMES = frozenset({"success", "failure", "superseded"})
PR_URL = re.compile(r"https://github[.]com/([^/]+/[^/]+)/pull/([1-9][0-9]*)")


def retired_outcome(payload: dict[str, Any]) -> str | None:
    """A retirement is immutable; malformed metadata must not reactivate a run."""
    if KEY not in payload:
        return None
    record = payload[KEY]
    if not isinstance(record, dict) or record.get("status") != "retired":
        raise ValueError("Invalid leader-lifecycle status")
    if not isinstance(record.get("outcome"), str) or record["outcome"] not in OUTCOMES:
        raise ValueError("Invalid retired Leader outcome")
    if not PR_URL.fullmatch(str(record.get("replacement-leader", ""))):
        raise ValueError("Retired Leader is missing a replacement PR")
    if not re.fullmatch(r"[0-9a-f]{40}", str(record.get("source-head", ""))):
        raise ValueError("Retired Leader is missing its source commit")
    try:
        stamp = datetime.fromisoformat(record["retired-at"].replace("Z", "+00:00"))
        if stamp.tzinfo is None:
            raise ValueError
    except (KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError("Retired Leader is missing a valid timestamp") from exc
    return record["outcome"]


def retire_leader(payload, *, replacement_url, source_sha, now=None):
    """Freeze the existing record; pending work is superseded, never failed."""
    from lib.gap_promotion_gates import derive_legacy_overall_status

    previous = retired_outcome(payload)
    if previous:
        return previous
    conclusion = (
        derive_legacy_overall_status(copy.deepcopy(payload))
        if "promotion-gates" in payload else payload.get("overall-status")
    )
    outcome = conclusion if conclusion in {"success", "failure"} else "superseded"
    record = {
        "status": "retired",
        "outcome": outcome,
        "replacement-leader": replacement_url,
        "source-head": source_sha,
        "retired-at": (now or datetime.now(timezone.utc)).isoformat(),
    }
    retired_outcome({KEY: record})
    payload[KEY] = record
    return outcome


def validate_replacement(leader_url, old, replacement_url, new):
    """Require an open old Leader and a newer open/merged batch on the same base."""
    old_ref, new_ref = PR_URL.fullmatch(leader_url), PR_URL.fullmatch(replacement_url)
    if not old_ref or not new_ref or old_ref[1] != new_ref[1] or int(new_ref[2]) <= int(old_ref[2]):
        raise ValueError("Replacement must be a newer Leader in the same repository")
    for index, pull in enumerate((old, new)):
        active = pull.get("state") == "open" and not pull.get("merged")
        replaced_by_completed = index == 1 and pull.get("state") == "closed" and pull.get("merged") is True
        labels = {label.get("name") for label in pull.get("labels", []) if isinstance(label, dict)}
        head = pull.get("head") or {}
        if (
            not (active or replaced_by_completed) or pull.get("draft")
            or "gated-artifacts-promoter" not in labels
            or not str(head.get("ref", "")).startswith("gap-leader/gap-")
            or (head.get("repo") or {}).get("full_name") != old_ref[1]
            or not re.fullmatch(r"[0-9a-f]{40}", str(head.get("sha", "")))
            or head["ref"].removeprefix("gap-leader/") not in labels
        ):
            raise ValueError("Rollover requires genuine non-draft Leaders and an open or merged replacement")
    old_base, new_base = old.get("base") or {}, new.get("base") or {}
    if (not old_base.get("ref") or old_base.get("ref") != new_base.get("ref")
            or (old_base.get("repo") or {}).get("full_name") != old_ref[1]
            or (new_base.get("repo") or {}).get("full_name") != old_ref[1]):
        raise ValueError("Replacement Leader must have the same repository and target branch")
    if old["head"]["ref"] == new["head"]["ref"]:
        raise ValueError("Replacement must represent a different promotion batch")
