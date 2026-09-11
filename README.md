# devops-shared-resources
A central repository for reusable DevOps assets: workflows, templates, scripts, configuration snippets, and shared automation resources that can be referenced across multiple projects.

## Branch sync tooling

Python libraries and a CLI for syncing one git branch into another, with optional PR creation.

- `scripts/sync_branches.py` — CLI entrypoint
- `lib/` — reusable modules (`config_parser`, `merge_resolver`, `pr_creator`)
- `docs/sync-branches/README.md` — usage and config format
- `docs/sync-branches/consumer-setup.md` — infra-repo config format (`git:` source map) and CI integration notes
- `tests/` — unit and local end-to-end tests (CI: `.github/workflows/sync-branches-tests.yml`, pytest only)
