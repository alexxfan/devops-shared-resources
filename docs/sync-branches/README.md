# Branch Sync Tooling

Python tooling for syncing one git branch into another, with optional PR creation.

## Layout

- `scripts/sync_branches.py` — CLI entrypoint
- `lib/config_parser.py` — config file parsing and validation
- `lib/merge_resolver.py` — merge logic with ignore-file handling
- `lib/pr_creator.py` — GitHub pull request creation and tracking-label updates

## Quick start

```bash
pip install -r requirements-dev.txt
export GITHUB_TOKEN="your-token"

python scripts/sync_branches.py \
  --source-repo https://github.com/org/repo.git \
  --source-branch main \
  --target-branch stable \
  --tracking-label lake-gate
```

## Config file

When `--config` is provided, per-entry sync flags must not also be passed on the CLI.

Run a subset of configured entries:

```bash
export GITHUB_TOKEN="your-token"

python scripts/sync_branches.py \
  --config config/sync-branches.yaml \
  --only kserve,kubeflow
```

### Native `syncs:` format

```yaml
syncs:
  - name: main-to-stable
    sync-type: pr
    src:
      url: https://github.com/org/repo.git
      branch: main
    dest:
      branch: stable
    ignore-files:
      - config/overrides.yaml
    pr:
      branch: sync-main-to-stable
      tracking-label: lake-gate
      labels:
        - automation
      automerge: true
      reviewers:
        - alice
```

`dest.url` defaults to `src.url` when omitted.

### Infra repo `git:` format

For orchestrator repos such as `rhods-devops-infra`, the existing component source map is supported:

```yaml
defaults:
  source-branch: main
  target-branch: stable
  tracking-label: lake-gate

git:
  - name: kserve
    automerge: "yes"
    repo-url: https://github.com/red-hat-data-services/kserve.git
    ignore-files: .tekton/*
```

See `docs/sync-branches/consumer-setup.md` for infra-repo config format and how the script is invoked from CI.

## Sync types

- `pr` (default): merge into a temporary branch and open or update a PR
- `push`: push a bootstrap branch without opening a PR
- `commit-merge`: merge directly into the target branch and push

## Testing

```bash
pytest -v
```
