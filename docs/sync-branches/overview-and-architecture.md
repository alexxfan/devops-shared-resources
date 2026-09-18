# Branch Sync Tooling — Overview and Architecture

This document describes the branch sync tooling in `devops-shared-resources`: what was built, why it was designed this way, and how each component works.

## Context
The goal is a script and supporting libraries that sync code between a source repo/branch and a target repo/branch. The primary mode creates a merge PR; alternative modes support direct push (bootstrapping) or direct merge commits.

Reference material (inspiration only, not ported line-for-line):
- GitHub Actions workflow syncing `main` → `stable` with a `lake-gate` tracking label
- [sync-git-branches](https://github.com/red-hat-data-services/sync-git-branches) bash entrypoint

## What Was Added
**`scripts/sync_branches.py`** — CLI with three sync modes:
- `pr` (default) — open/update a PR; head can be a sync branch (`--pr-head sync-branch`) or the source branch (`--pr-head source`)
- `push` — bootstrap a target branch without a PR
- `commit-merge` — merge directly into the target branch

**`lib/`** — reusable modules:
- `config_parser.py` — YAML parsing, validation, `dest.url` defaulting, `ignore-files` normalization
- `merge_resolver.py` — merge with ignore-file handling (target wins on conflict; `--no-ff` when ignore patterns set)
- `pr_creator.py` — GitHub PR create/update, tracking-label reuse, automerge via GraphQL
- `git_utils.py` — clone, fetch, push, low-level git helpers

**`tests/`** — 20 unit and local e2e tests (bare git remotes).
**`.github/workflows/sync-branches-tests.yml`** — runs pytest on PRs touching this code.

## Example Usage
```bash
pip install -r requirements-dev.txt
export GITHUB_TOKEN="$GITHUB_TOKEN"
python scripts/sync_branches.py \
  --source-repo https://github.com/org/repo.git \
  --source-branch main \
  --target-branch stable \
  --pr-head source \
  --tracking-label lake-gate \
  --dry-run
```

`--pr-head source` opens `main` → `stable` directly. Omit it (or use `sync-branch`) to merge into a temporary sync branch first.

**Config file** — when `--config` is set, do not also pass `--source-repo`, `--source-branch`, etc.
```yaml
syncs:
  - sync-type: pr
    src:
      url: https://github.com/org/repo.git
      branch: main
    dest:
      branch: stable
    ignore-files:
      - config/overrides.yaml
    pr:
      tracking-label: lake-gate
      automerge: true
```
`dest.url` defaults to `src.url` when omitted. See `docs/sync-branches/README.md`.

## Why Python?
1. Requirements called for libraries + tests — reusable `/lib` modules, pytest, callable from CLI or Actions.
2. Git merge/conflict/attribute logic is best delegated to the `git` CLI rather than GitPython.
3. YAML config parsing, validation, and normalization are easier to test in Python than bash.
4. Fills a gap in this repo: complex testable logic that multiple consumers can import.

## Overall Architecture
Thin orchestrator (`scripts/sync_branches.py`) over domain libraries + shared git utilities:
```
scripts/sync_branches.py  (orchestrator)
        ├── lib/config_parser.py
        ├── lib/merge_resolver.py
        ├── lib/pr_creator.py  ──► GitHub REST / GraphQL
        └── lib/git_utils.py   ──► git CLI (subprocess)
```
Each library owns one concern. Consumers can shell out to the script or import `merge_branches()` / `PRCreator` directly.

## How a Sync Run Flows (`sync-type: pr`)
1. Parse input — CLI or YAML → normalized dict per entry
2. Prepare worktree — clone target, checkout target branch, fetch source
3. Merge — source into PR branch (or target branch for `commit-merge`)
4. Push — working branch to remote
5. Create/update PR — GitHub API (if mode is `pr`)

```
User → sync_branches.py
  → config_parser.load_sync_config()
  → git_utils: clone + fetch
  → merge_resolver.merge_branches()
  → git_utils: push
  → pr_creator.create_or_update_tracking_pr() → PR URL
```

## File-by-File Breakdown

### `scripts/sync_branches.py`
Only user-facing entrypoint. No merge or GitHub logic inside.
- CLI parsing via `argparse`
- Input: CLI flags **or** `--config` (mutually exclusive for per-entry fields)
- `run_sync_entry()` drives the pipeline per config entry
- Temp clone per run, cleaned up in `finally`
- Key helpers: `_validate_cli_exclusivity()`, `_prepare_worktree()`, `_default_pr_branch()`

### `lib/config_parser.py`
YAML → stable internal dict. Normalization rules:
- `ignore-files: "a b"` or `["a","b"]` → `ignore_files: ["a","b"]`
- missing `dest.url` → `src.url`
- missing `sync-type` → `"pr"`
- `tracking-label` / `tracking_label` both accepted
Validates `src.url`, `src.branch`, `dest.branch`. Supports single entry, list, or `{ syncs: [...] }`. Extend here for RHOAIENG-93328 schema changes.

### `lib/merge_resolver.py`
Merge source into target while keeping ignored files from target.
1. Write `pattern merge=ours` to `.git/info/attributes`
2. Git prefers target ("ours") on matching paths
3. Auto-resolve conflicts on ignored files; fail on others
4. `_restore_excluded_files()` reverts any ignored paths that still drifted
5. `--no-ff` when ignore patterns exist (prevents FF skipping ignore logic)
Returns `MergeResult(merged, already_up_to_date, message)`.

### `lib/pr_creator.py`
General-purpose GitHub PR helper (reusable outside sync).
- `find_open_pr_by_label()` — find tracking PR
- `create_pull_request()` — create PR
- `create_or_update_tracking_pr()` — update if label matches, else create
- `enable_automerge()` — GraphQL (REST lacks this)
- `add_labels()`, `request_reviewers()`
Uses `requests`, not `gh` CLI — portable in CI, easy to mock.

### `lib/git_utils.py`
All git via `run_git()` → `GitCommandError` on failure. Handles local vs remote URLs, token auth, `owner/repo` parsing, glob path matching.

### `lib/__init__.py`
Public re-exports: `from lib import merge_branches, PRCreator, load_sync_config`

## Tests and CI
- `tests/test_config_parser.py` — validation, defaults, normalization
- `tests/test_merge_resolver.py` — merge, ignore-files, conflicts
- `tests/test_pr_creator.py` — GitHub API (mocked)
- `tests/test_sync_branches.py` — CLI, dry-run, orchestration (mocked)
- `tests/test_e2e_local_sync.py` — bare remote e2e
- `tests/conftest.py` — git repo factory fixture

E2E uses bare remotes because pushing to a checked-out non-bare repo fails.

## Design Decisions
- **Subprocess git vs GitPython** — reliable merge/attributes; tradeoff: less programmatic repo access
- **REST API vs gh CLI** — no extra binary, easy to mock; tradeoff: maintain GraphQL for automerge
- **Normalized config dict** — shared internal contract; tradeoff: translation layer in parser
- **Temp clone per run** — isolated, CI-safe; tradeoff: slower
- **Tracking label reuses PR** — avoids PR spam; tradeoff: force-push, branch name may differ
- **`commit-merge` included** — stretch goal, low cost once merge exists; tradeoff: more test paths

## Sync Types
**`pr`** — clone target → checkout/create PR branch → fetch source → merge → push → create/update PR. Tracking label: update existing open PR + force-push branch.
**`push`** — clone source, create target branch, force-push. No merge, no PR.
**`commit-merge`** — same as PR through merge, push directly to target branch. No PR.

## Normalized Config Entry (internal shape)
```python
{
    "name": "main-to-stable",
    "sync_type": "pr",
    "src": {"url": "https://github.com/org/repo.git", "branch": "main"},
    "dest": {"url": "https://github.com/org/repo.git", "branch": "stable"},
    "ignore_files": ["config/overrides.yaml"],
    "pr": {
        "branch": "sync-main-to-stable",
        "tracking_label": "lake-gate",
        "labels": ["automation"],
        "automerge": True,
        "title": None, "body": None,
        "reviewers": ["alice"],
    },
    "merge_args": [], "fetch_args": [], "push_args": [],
}
```

## Consumer Repo Workflow (rhods-devops-infra)
The GitHub Actions workflow lives in the infra repo, not here. It checks out this repo at runtime:

```
rhods-devops-infra workflow
  → checkout infra repo (config/sync-branches.yaml)
  → checkout devops-shared-resources
  → pip install -r requirements.txt
  → SYNC_TOKEN=$SYNC_TOKEN python scripts/sync_branches.py --config config/sync-branches.yaml --only kserve
```

See `docs/sync-branches/consumer-setup.md`. The workflow YAML itself is owned by `rhods-devops-infra`.

## Repository Layout
```
devops-shared-resources/
├── scripts/sync_branches.py
├── lib/{__init__,config_parser,merge_resolver,pr_creator,git_utils}.py
├── tests/{conftest,test_*}.py
├── docs/sync-branches/{README,overview-and-architecture}.md
├── requirements.txt, requirements-dev.txt, pyproject.toml
└── .github/workflows/sync-branches-tests.yml
```

## Open Items
- Extend `config_parser.py` if RHOAIENG-93328 adds fields; downstream code should not change.
- Tracking label updates PR metadata and force-pushes sync branch.
- Consumer-repo workflow is maintained in `rhods-devops-infra`, not in this repository.
