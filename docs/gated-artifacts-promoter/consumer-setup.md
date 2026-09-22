# Consumer setup (rhods-devops-infra)

The **workflow and config** live in `rhods-devops-infra`. This repository provides the
Python orchestrator and libraries.

## What lives where

| rhods-devops-infra | devops-shared-resources |
|--------------------|-------------------------|
| `.github/workflows/gated-artifacts-promoter.yaml` | `scripts/run_gated_artifacts_promoter.py` |
| `src/config/gated-artifacts-promoter.yaml` | `lib/trigger_id.py`, `lib/state_file.py`, `lib/leader_pr.py` |

## Workflow contract

The infra workflow stays dumb: checkout, token, install deps, run one script.
All GAP defaults (`.tekton/*` ignore, source→stable, tracking labels) live in
the orchestrator.

```bash
export GITHUB_TOKEN="<token>"
export GH_TOKEN="$GITHUB_TOKEN"

python devops-shared-resources/scripts/run_gated_artifacts_promoter.py \
  --config src/config/gated-artifacts-promoter.yaml \
  [--trigger-id "$TRIGGER_ID"] \
  [--only "$ONLY"] \
  [--dry-run]
```

Failed workflow runs print the same command (plus clone lines) in the job log
and step summary so you can replay locally.
