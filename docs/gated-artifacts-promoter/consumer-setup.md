# Consumer setup (rhods-devops-infra)

The **workflow and config** live in `rhods-devops-infra`. This repository provides the
Python orchestrator and libraries.

## What lives where

| rhods-devops-infra | devops-shared-resources |
|--------------------|-------------------------|
| `.github/workflows/gated-artifacts-promoter.yaml` | `scripts/run_gated_artifacts_promoter.py` |
| `src/config/gated-artifacts-promoter.yaml` | `lib/trigger_id.py`, `lib/state_file.py`, `lib/leader_pr.py` |

## Workflow contract

The infra workflow should stay dumb:

1. Generate a GitHub App token (`DEVOPS_APP_ID` / `DEVOPS_APP_PRIVATE_KEY`)
2. Checkout infra + `devops-shared-resources`
3. `pip install -r devops-shared-resources/requirements.txt`
4. Run:

```bash
export GITHUB_TOKEN="<app-token>"
export GH_TOKEN="$GITHUB_TOKEN"

python devops-shared-resources/scripts/run_gated_artifacts_promoter.py \
  --config src/config/gated-artifacts-promoter.yaml \
  [--trigger-id "$TRIGGER_ID"] \
  [--only "$ONLY"] \
  [--dry-run]
```

## Local reproduction of a failed run

```bash
git clone git@github.com:red-hat-data-services/rhods-devops-infra.git
git clone git@github.com:red-hat-data-services/devops-shared-resources.git
export GITHUB_TOKEN=... GH_TOKEN=...

python devops-shared-resources/scripts/run_gated_artifacts_promoter.py \
  --config rhods-devops-infra/src/config/gated-artifacts-promoter.yaml \
  --only kserve \
  --dry-run
```

Use the same `--trigger-id` printed in the failed workflow logs when correlating
a run to its Leader `state.json` path. Child sync PRs are reused via the
`gated-artifacts-promoter` tracking label (re-runs update open PRs).
