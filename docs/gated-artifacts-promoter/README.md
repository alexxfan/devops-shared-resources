# Gated Artifacts Promoter

Orchestrates main→stable (or configured) branch syncs via `scripts/sync_branches.py`,
then opens a **Leader PR** in `red-hat-data-services/gated-artifacts-promoter` that
tracks every child sync PR for a given trigger ID.

## Layout

- `scripts/run_gated_artifacts_promoter.py` — CLI orchestrator
- `lib/trigger_id.py` — unique trigger ID generator
- `lib/state_file.py` — `state.json` build/validate helpers
- `lib/leader_pr.py` — Leader PR create/update via `gh`

## Quick start

```bash
pip install -r requirements-dev.txt
export GITHUB_TOKEN="your-token"   # or SYNC_TOKEN
export GH_TOKEN="$GITHUB_TOKEN"    # required for Leader PR (gh CLI)

python scripts/run_gated_artifacts_promoter.py \
  --config /path/to/gated-artifacts-promoter.yaml \
  --only kserve \
  --dry-run
```

Omit `--trigger-id` to generate one (used for `<trigger_id>/state.json` and as
an extra PR label). Child sync PRs and the Leader PR are **tracked** by the
shared `gated-artifacts-promoter` label, so re-runs update existing open PRs
instead of opening new ones.

## Config

Uses the same formats as branch sync (`git:` infra map or native `syncs:`). The
orchestrator **overrides** each entry's tracking label with the trigger ID and always
adds the `gated-artifacts-promoter` label.

Example infra config:

```yaml
defaults:
  source-branch: main
  target-branch: stable
  pr-head: sync-branch

git:
  - name: kserve
    automerge: "no"
    repo-url: https://github.com/rhoai-rhtap/kserve.git
    ignore-files: .tekton/*
```

## Outputs

Stdout includes:

- `trigger_id=...`
- Per-entry sync messages and child PR URLs
- Leader PR URL (or dry-run summary)

Leader PR path: `<trigger_id>/state.json`

Stage 1 `state.json` shape ([RHOAIENG-93564](https://redhat.atlassian.net/browse/RHOAIENG-93564)):

```json
{
  "pull-requests": [
    {
      "repo": "kserve",
      "pr-url": "https://github.com/org/kserve/pull/1",
      "pr-status": "new",
      "builds": []
    }
  ]
}
```

## Consumer workflow

The GitHub Actions workflow lives in `rhods-devops-infra`:

1. Checkout infra (for config)
2. Checkout this repo
3. Install requirements
4. Run `scripts/run_gated_artifacts_promoter.py`

See `docs/gated-artifacts-promoter/consumer-setup.md`.
