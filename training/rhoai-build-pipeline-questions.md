# RHOAI Build Pipeline — Training Questions & Prompts

A curated set of questions to build a thorough understanding of the RHOAI build pipeline. Ask these to the Ops Buddy bot (or use them as self-study prompts). Organized from foundational concepts → specific build types → automation & troubleshooting.

---

## 🔗 1 — The End-to-End Build Chain

1. *Walk me through the entire RHOAI build chain from a component code push all the way to the FBC image on Quay. What are all the stages?*
2. *What is a "nudge PR" in Konflux, how does it get created, and what triggers it?*
3. *What is the difference between a Konflux pull-request (pre-merge) build and a push (post-merge) build? Which one triggers nudges?*
4. *What role does `build-nudges-ref` play in the component spec, and what happens if a component is missing from that list?*

---

## 🧱 2 — Component Builds

5. *How does a component build work in Konflux for RHOAI? What pipeline is used, and where do the built images land?*
6. *What is Pipelines-as-Code (PaC) and how do the `.tekton/` pipeline definitions control component builds?*
7. *How do shared/external components (e.g. from AIPCC) get synced into RHOAI? What is the RHOAI Sync Agent and how often does it run?*
8. *What does Renovate do for RHOAI component Dockerfiles, and how are base-image digest updates handled?*

---

## ⚙️ 3 — Operator Builds

9. *After a component image is built, how does the operator get updated? What do `operator-nudging.yaml`, `operands-map.yaml`, and `manifests-config.yaml` each do?*
10. *What is `operator-processor.yaml` and what does it do when triggered by a nudge merge?*
11. *How does the Early Gate (EG) pipeline work for operator pull requests? What tasks does it run and what does it validate?*
12. *What does the `/build-konflux` comment do on an operator PR, and how does the EG pipeline prevent infinite commit loops?*

---

## 📦 4 — Bundle Builds

13. *How does the bundle build get triggered after a successful operator build? What is `bundle-patch.yaml` in RHOAI-Build-Config?*
14. *What does the bundle-processor do — how does it inject digests into the CSV `relatedImages`?*
15. *What is the relationship between the operator build, the bundle build, and the OLM release lifecycle?*

---

## 📂 5 — FBC (File-Based Catalog) Builds

16. *What is the FBC fragment and how is it built? What pipeline and Konflux application are used?*
17. *What does the `validate-fbc` task check, and why must all `olm.bundle` references be digest-pinned?*
18. *How does `catalog-patch.yaml` trigger the FBC build, and what does the FBC-processor do with `catalog.yaml`?*
19. *How does the FBC fragment get merged into the production index image (IIB)?*

---

## 🤖 6 — Auto-Merge & Nudge Automation

20. *What is `operator-insta-merge.yaml` and how fast does it merge nudge PRs? Are there any required status checks?*
21. *What are the known weaknesses of the current nudge auto-merge — race conditions, token expiry, Quay tag floating?*
22. *What is the difference between InstaMerge, Renovate auto-merge, and Tide for merging nudge PRs?*
23. *What improvements are planned for gating auto-merge on build verification (e.g. a `verify-source-build` check)?*

---

## 🌙 7 — CI vs. Nightly Builds

24. *What is the difference between a CI build and a nightly build in RHOAI? Is a nightly a separate build or just a tag promotion?*
25. *What does the Jenkins `nightly-autotrigger` job do, and what gate tests does it run (e.g. `rhoai-nightly-bvt`)?*
26. *How does nightly tagging work — what images get the `odh-stable` vs. `odh-stable-nightly` tags?*
27. *What is the RC (Release Candidate) acceptance criteria — which tests must pass before an RC is considered accepted?*
28. *How do nightly builds feed into the stage and production release process?*

---

## 🔍 8 — Troubleshooting & Observability

29. *A component was merged but the operator hasn't rebuilt — how do I trace what went wrong in the nudge chain?*
30. *Where can I find build notifications for RHOAI, and what channels should I watch?*
31. *How do I look up a specific PipelineRun for a component or operator build on Konflux?*
32. *A nudge PR was created but never merged — what are the common causes?*
33. *The FBC build is failing with a digest validation error — what should I check?*

---

## 🌳 9 — Branching & Release Strategy

34. *How does the RHOAI branching model work — what is the relationship between ODH `main`, RHDS `main`, and `rhoai-x.y` branches?*
35. *How does code flow from upstream ODH to the release branch, and what changes at code freeze?*
36. *How are CVE fixes propagated through the entire build chain (component → bundle → FBC)?*

---

> **Tip:** You can ask the Ops Buddy bot any of these questions verbatim. For some areas (especially nightly scheduling details and branching specifics), the bot will flag where its information comes from less-verified sources so you can cross-check.
>
> You can also ask follow-ups like *"Show me the actual nudge config for component X"* or *"What PipelineRuns ran for the operator today?"* — the bot can look those up live from KubeArchive and Konflux.
