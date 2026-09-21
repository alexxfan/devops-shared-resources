# devops-shared-resources
A central repository for reusable DevOps assets: workflows, templates, scripts, configuration snippets, and shared automation resources that can be referenced across multiple projects.

## Branch sync tooling

Python libraries and a CLI for syncing one git branch into another, with optional PR creation.

- `scripts/sync_branches.py` — CLI entrypoint (`--pr-head sync-branch|source`)
- `lib/` — reusable modules (`config_parser`, `merge_resolver`, `pr_creator`)
- `docs/sync-branches/README.md` — usage, config format, and PR head strategies
- `docs/sync-branches/consumer-setup.md` — infra-repo config format (`git:` source map) and CI integration notes
- `tests/` — unit and local end-to-end tests (CI: `.github/workflows/sync-branches-tests.yml`, pytest only)

## Gated Artifacts Promoter orchestrator (RHOAIENG-93524)

Runs configured branch syncs with a shared trigger ID, then opens a Leader PR that tracks child PRs.

- `scripts/run_gated_artifacts_promoter.py` — CLI orchestrator
- `lib/trigger_id.py`, `lib/state_file.py`, `lib/leader_pr.py` — reusable libraries
- `docs/gated-artifacts-promoter/README.md` — usage
- `docs/gated-artifacts-promoter/consumer-setup.md` — infra workflow contract

## PR status updater (GAP)

Posts GitHub commit statuses for Gated Artifacts Promoter collaborator PRs (RHOAIENG-93330).

- `lib/pr_status_updater.py` — reusable library (`gh api` commit statuses)
- `scripts/post_pr_status.py` — manual CLI
- `docs/pr-status-updater/README.md` — usage
- `tests/` — unit tests (`pytest`; see docs)
