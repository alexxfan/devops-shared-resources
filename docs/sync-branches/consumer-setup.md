# Consumer Repo Setup (rhods-devops-infra)

The **GitHub Actions workflow and config** belong in the infra repo. This repo only provides the Python script and libraries.

## What lives where

| rhods-devops-infra | devops-shared-resources |
|--------------------|-------------------------|
| `.github/workflows/sync-branches.yaml` | `scripts/sync_branches.py` |
| `config/sync-branches.yaml` | `lib/`, `tests/`, docs |

The infra workflow should:

1. Check out the infra repo (for config)
2. Check out `devops-shared-resources` into a subdirectory
3. `pip install -r devops-shared-resources/requirements.txt`
4. Run the script against the infra config

```bash
python devops-shared-resources/scripts/sync_branches.py \
  --config config/sync-branches.yaml \
  --only kserve \
  --token "$SYNC_TOKEN"
```

Use `--only` with a comma-separated list of component names, or omit it to run every entry.

## Config format

Use the existing `git:` source map in the infra repo:

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
  - name: kubeflow
    automerge: "yes"
    repo-url: https://github.com/red-hat-data-services/kubeflow.git
    ignore-files: .tekton/*
  - name: rhoai-additional-images
    automerge: "no"
    repo-url: https://github.com/red-hat-data-services/rhoai-additional-images.git
```

### Field mapping

| Infra config field | Script behavior |
|--------------------|-----------------|
| `name` | Sync entry name; used with `--only` |
| `repo-url` | Source and target repository URL |
| `automerge` | `"yes"` / `"no"` → PR automerge |
| `ignore-files` | Comma- or space-separated glob patterns |
| `defaults.source-branch` | Source branch (default: `main`) |
| `defaults.target-branch` | Target branch (default: `stable`) |
| `defaults.tracking-label` | PR tracking label (default: `lake-gate`) |

Per-entry overrides are also supported: `source-branch`, `target-branch`, `tracking-label`, `sync-type`.

The native `syncs:` format from `docs/sync-branches/README.md` is still supported.

## Token requirements

`SYNC_TOKEN` needs permission to:

- read and clone target repositories
- push branches
- create and update pull requests and labels

## Local test

```bash
git clone https://github.com/red-hat-data-services/devops-shared-resources.git

python devops-shared-resources/scripts/sync_branches.py \
  --config config/sync-branches.yaml \
  --only kserve \
  --token "$GITHUB_TOKEN" \
  --dry-run
```
