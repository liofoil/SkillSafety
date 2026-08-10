# Artifact notice

## Public research artifact

This repository is a public research artifact accompanying
[*Poise: Position-Aware One-Instruction Skill Injection for Silent Execution
on LLM Agents*](https://arxiv.org/abs/2606.07943).

The authors have not yet selected a source-code license for this artifact.
Until a license is added, the repository is source-available for inspection,
but no additional permission to copy, modify, or redistribute its contents is
granted. Nothing in this notice chooses a license or changes the terms of
third-party material.

## Included

- source for POISE, YAML-only, and B@k=2 variant construction;
- bounded, postcondition-gated canary scripts and a synthetic honey
  workspace;
- sandbox experiment launchers and result aggregation;
- explicit task-to-skill manifests for the 25-task Skill-Inject and
  27-task SkillsBench eligible pools;
- recovered benchmark commit identifiers and recomputed byte inventories;
- the vendored OpenClaw adapter and exact observed runtime version;
- reproduction instructions.

## Not included

- Skill-Inject or SkillsBench task files and their assets;
- model weights, hosted model snapshots, API credentials, or service
  quotas;
- the original generated poisoned skills;
- the original trial trajectories, verifier outputs, or result CSV files;
- a result bundle from which the paper's reported numerical tables can be
  recomputed directly;

The benchmark inputs must be acquired from their official distributions
and remain governed by their own licenses and asset-specific notices. Use
the commits and inventories in `manifests/BENCHMARK_SNAPSHOTS.md`; do not
copy benchmark tasks into this artifact without separately checking every
applicable license.

## Safety boundary

The artifact contains deliberately adversarial skill text and scripts that
simulate three bounded harm categories. The supplied workspace contains
synthetic honey-tagged strings, not credentials. Canary success is recorded
inside the disposable sandbox only after an expected local action and
postcondition check.

The reported experiments used short-timeout HTTP POST attempts to an
author-controlled endpoint with no receiving service, but network delivery
was not part of the success criterion. For safe distribution, the included
canaries remove those outbound attempts and serialize the would-be requests
locally.

Use only isolated, disposable test environments. Do not run poisoned
skills or payload scripts against personal files, production systems, real
credentials, or a routable collection endpoint.

## Interpretation

The offline tests check deterministic construction and aggregation
semantics; they do not exercise a model or establish the paper's empirical
rates. A new end-to-end run requires third-party benchmarks and paid hosted
services and may differ because model sampling and services change. The
release fixes recovered software and benchmark versions, but the absence
of the original generated skills and trial-level outputs prevents direct
recomputation of the reported tables from stored evidence.

This research artifact is provided without warranty for research inspection
and verification purposes.
