"""Adapt shared Konflux observations to the existing GAP gate/result contract."""
from __future__ import annotations

from datetime import datetime, timezone
from lib.gap_check_selection import cluster_from_check_name
from lib.github_check_runs import CheckFinding, GateResult, OUTCOME_FAIL, OUTCOME_PASS, OUTCOME_PENDING, latest_matching
from lib.konflux_builds import (
    KonfluxClient,
    KonfluxQueryError,
    cluster_id_from_check_runs,
    default_konflux_cluster,
)


def _pipeline_entry(item):
    """Build a required-pipelines evidence entry, handling multi-name alternatives."""
    entry = {"component": item.key}
    suffixes = getattr(item, "name_suffixes", None)
    if suffixes and len(suffixes) > 1:
        entry["pipeline_names"] = list(suffixes)
        entry["pipeline_name"] = suffixes[0]
    elif suffixes:
        entry["pipeline_name"] = suffixes[0]
    else:
        entry["pipeline_name"] = item.name_suffix
    return entry


def _pipeline_names_for(item):
    """Return the set of pipeline names to pass to KonfluxClient.observe."""
    suffixes = getattr(item, "name_suffixes", None)
    if suffixes:
        return set(suffixes)
    suffix = getattr(item, "name_suffix", None)
    if suffix:
        return {suffix}
    return None


def evaluate_konflux_gate(
    *,
    repo,
    number,
    sha,
    expected,
    client=None,
    query_error=None,
    cel_evaluated=True,
    cel_error=None,
    cluster=None,
    check_runs=None,
    prefetched_clients=None,
):
    """Translate expected Konflux builds into a gate verdict and evidence receipt.

    When ``client`` is omitted, pick credentials/endpoints for ``cluster``, or
    derive the cluster from GitHub check-run app slugs (P01 vs P02 cutover).
    """
    evidence = {"source": "konflux", "head-sha": sha, "repository": repo, "pr": number,
                "observed-at": datetime.now(timezone.utc).isoformat(),
                "expected-components": sorted({item.key for item in expected}),
                "cel-evaluated": bool(cel_evaluated),
                "runs": []}
    if cel_error:
        evidence["cel-error"] = str(cel_error)
    evidence["required-pipelines"] = {
        "schema": 1, "source": "revision-pinned-tekton", "repository": repo,
        "pr": number, "head-sha": sha,
        "pipelines": [_pipeline_entry(item) for item in expected],
    }
    findings = []
    try:
        if query_error:
            raise KonfluxQueryError(query_error)

        # Determine per-component cluster from check run name prefixes.
        # Skip inference when an explicit cluster parameter is set.
        component_clusters: dict[str, str] = {}
        if expected and client is None and check_runs and not cluster:
            for item in expected:
                try:
                    matched = latest_matching(item, check_runs)
                except (AttributeError, TypeError):
                    matched = None
                if matched:
                    run_name = getattr(matched, "name", "")
                    run_cluster = cluster_from_check_name(run_name) if run_name else None
                    if run_cluster:
                        component_clusters[item.key] = run_cluster

        default_cluster_id: str | None = None
        clients_cache: dict[str, KonfluxClient] = dict(prefetched_clients) if prefetched_clients else {}

        if expected:
            if client is None:
                default_cluster_id = cluster or (
                    cluster_id_from_check_runs(check_runs)
                    if check_runs is not None
                    else default_konflux_cluster()
                )
                evidence["konflux-cluster"] = default_cluster_id
                if component_clusters:
                    evidence["component-clusters"] = dict(component_clusters)
            elif cluster:
                evidence["konflux-cluster"] = cluster

        for item in expected:
            component = item.key
            if client is not None:
                comp_client = client
            else:
                comp_cluster = component_clusters.get(component, default_cluster_id)
                if comp_cluster not in clients_cache:
                    clients_cache[comp_cluster] = KonfluxClient.for_cluster(comp_cluster)
                comp_client = clients_cache[comp_cluster]
            observation = comp_client.observe(repo=repo, number=number, sha=sha, components={component},
                                              pipeline_names=_pipeline_names_for(item))
            evidence["runs"].append(observation.to_dict())
            outcome = OUTCOME_PASS if observation.status == "success" else (
                OUTCOME_FAIL if observation.status in {"failure", "timed_out"} else OUTCOME_PENDING)
            findings.append(CheckFinding(
                key=component, outcome=outcome,
                reason=f"Konflux {observation.status}: {observation.reason}",
                link=observation.url or None, revision=sha,
                status=observation.status, conclusion=observation.status,
                name=observation.name or component,
            ))
    except (KonfluxQueryError, ValueError, TypeError) as exc:
        evidence["query-error"] = str(exc)
        findings.append(CheckFinding(key="konflux-query", outcome=OUTCOME_PENDING,
                                     reason=str(exc), revision=sha, status="unknown"))
    outcome = OUTCOME_FAIL if any(f.outcome == OUTCOME_FAIL for f in findings) else (
        OUTCOME_PENDING if any(f.outcome == OUTCOME_PENDING for f in findings) else OUTCOME_PASS)
    link = next((f.link for f in findings if f.outcome == outcome and f.link), None)
    return GateResult(outcome=outcome, findings=tuple(findings), timed_out=False, link=link), evidence
