"""Build and validate Gated Artifacts Promoter state.json files."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

SCHEMA_VERSION = 1
DEFAULT_LEADER_REPO = "red-hat-data-services/gated-artifacts-promoter"


class StateFileError(ValueError):
    """Raised when a state file payload is invalid."""


@dataclass(frozen=True)
class StatePullRequest:
    name: str
    repo: str
    url: str
    number: int

    @classmethod
    def from_mapping(cls, data: dict[str, Any], *, index: int) -> "StatePullRequest":
        for key in ("name", "repo", "url", "number"):
            if key not in data:
                raise StateFileError(f"prs[{index}].{key} is required")
        number = data["number"]
        if not isinstance(number, int) or isinstance(number, bool) or number <= 0:
            raise StateFileError(f"prs[{index}].number must be a positive integer")
        name = str(data["name"]).strip()
        repo = str(data["repo"]).strip()
        url = str(data["url"]).strip()
        if not name:
            raise StateFileError(f"prs[{index}].name must be non-empty")
        if "/" not in repo:
            raise StateFileError(f"prs[{index}].repo must be owner/name")
        if not _looks_like_pr_url(url):
            raise StateFileError(
                f"prs[{index}].url must be https://github.com/<owner>/<repo>/pull/<n>"
            )
        return cls(name=name, repo=repo, url=url, number=number)


@dataclass(frozen=True)
class StateLeader:
    repo: str
    path: str

    @classmethod
    def from_mapping(cls, data: dict[str, Any]) -> "StateLeader":
        if not isinstance(data, dict):
            raise StateFileError("leader must be a mapping")
        repo = str(data.get("repo") or "").strip()
        path = str(data.get("path") or "").strip()
        if not repo or "/" not in repo:
            raise StateFileError("leader.repo must be owner/name")
        if not path:
            raise StateFileError("leader.path must be non-empty")
        return cls(repo=repo, path=path)


@dataclass
class PromoterState:
    trigger_id: str
    prs: list[StatePullRequest] = field(default_factory=list)
    created_at: str = ""
    schema_version: int = SCHEMA_VERSION
    leader: StateLeader | None = None

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        if self.leader is None:
            self.leader = StateLeader(
                repo=DEFAULT_LEADER_REPO,
                path=f"{self.trigger_id}/state.json",
            )

    def to_dict(self) -> dict[str, Any]:
        assert self.leader is not None
        return {
            "schema_version": self.schema_version,
            "trigger_id": self.trigger_id,
            "created_at": self.created_at,
            "leader": asdict(self.leader),
            "prs": [asdict(pr) for pr in self.prs],
        }

    def to_json(self, *, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=False) + "\n"


def state_path_for_trigger(trigger_id: str) -> str:
    """Return the Leader-repo relative path for a trigger's state.json."""
    return f"{trigger_id}/state.json"


def build_state(
    *,
    trigger_id: str,
    prs: list[StatePullRequest] | list[dict[str, Any]],
    leader_repo: str = DEFAULT_LEADER_REPO,
    created_at: str | None = None,
) -> PromoterState:
    """Build a validated PromoterState from PR records."""
    normalized_prs: list[StatePullRequest] = []
    for index, item in enumerate(prs):
        if isinstance(item, StatePullRequest):
            normalized_prs.append(item)
        elif isinstance(item, dict):
            normalized_prs.append(StatePullRequest.from_mapping(item, index=index))
        else:
            raise StateFileError(f"prs[{index}] must be a mapping")

    state = PromoterState(
        trigger_id=trigger_id,
        prs=normalized_prs,
        created_at=created_at or "",
        leader=StateLeader(repo=leader_repo, path=state_path_for_trigger(trigger_id)),
    )
    validate_state(state.to_dict())
    return state


def validate_state(data: Any) -> dict[str, Any]:
    """Validate a state.json payload and return a normalized dict."""
    if not isinstance(data, dict):
        raise StateFileError("state must be a JSON object")

    schema_version = data.get("schema_version")
    if schema_version != SCHEMA_VERSION:
        raise StateFileError(
            f"schema_version must be {SCHEMA_VERSION}, got {schema_version!r}"
        )

    trigger_id = str(data.get("trigger_id") or "").strip()
    if not trigger_id:
        raise StateFileError("trigger_id is required")

    created_at = str(data.get("created_at") or "").strip()
    if not created_at:
        raise StateFileError("created_at is required")

    leader = StateLeader.from_mapping(data.get("leader") or {})
    expected_path = state_path_for_trigger(trigger_id)
    if leader.path != expected_path:
        raise StateFileError(
            f"leader.path must be '{expected_path}', got '{leader.path}'"
        )

    raw_prs = data.get("prs")
    if not isinstance(raw_prs, list):
        raise StateFileError("prs must be a list")

    prs = [
        StatePullRequest.from_mapping(item, index=index)
        if isinstance(item, dict)
        else (_raise_pr_type(index))
        for index, item in enumerate(raw_prs)
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "trigger_id": trigger_id,
        "created_at": created_at,
        "leader": asdict(leader),
        "prs": [asdict(pr) for pr in prs],
    }


def write_state_file(path: str | Path, state: PromoterState | dict[str, Any]) -> Path:
    """Write state.json to path after validation."""
    payload = state.to_dict() if isinstance(state, PromoterState) else validate_state(state)
    validate_state(payload)
    out = Path(path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return out


def load_state_file(path: str | Path) -> dict[str, Any]:
    """Load and validate a state.json file from disk."""
    content = Path(path).read_text(encoding="utf-8")
    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise StateFileError(f"invalid JSON in {path}: {exc}") from exc
    return validate_state(data)


def parse_pr_number_from_url(url: str) -> int:
    """Extract the pull request number from a GitHub PR URL."""
    parsed = urlparse(url.strip())
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) < 4 or parts[2] != "pull":
        raise StateFileError(f"not a GitHub pull request URL: {url}")
    try:
        return int(parts[3])
    except ValueError as exc:
        raise StateFileError(f"invalid pull request number in URL: {url}") from exc


def repo_slug_from_url(repo_url: str) -> str:
    """Return owner/name for a GitHub HTTPS/SSH URL or slug."""
    text = repo_url.strip().removesuffix(".git")
    if text.startswith("git@github.com:"):
        return text.split(":", 1)[1]
    if text.startswith("http://") or text.startswith("https://"):
        path = urlparse(text).path.lstrip("/")
        return path.removesuffix(".git")
    if "/" in text:
        return text
    raise StateFileError(f"cannot derive repo slug from '{repo_url}'")


def _looks_like_pr_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.netloc != "github.com":
        return False
    parts = [part for part in parsed.path.split("/") if part]
    return len(parts) >= 4 and parts[2] == "pull" and parts[3].isdigit()


def _raise_pr_type(index: int) -> StatePullRequest:
    raise StateFileError(f"prs[{index}] must be a mapping")
