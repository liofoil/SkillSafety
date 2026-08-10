#!/bin/bash
# One matrix cell: method × agent × benchmark.
#
# Usage:
#   bash scripts/matrix/run_cell.sh <method> <agent> <benchmark>
#
# Args:
#   method     poise | yamlonly | bk2
#   agent      codex | openclaw-flash | openclaw-pro | claude-code
#   benchmark  SI | SB
#
# Reads sample.csv from samples/<benchmark>/<method>.csv and writes
# results to results/<bench>-<method>-<agent>/.
#
# Two sandbox attempts per (task, harm) variant: POISE/YAML replay one
# constructed skill twice, while B@k=2 evaluates each of two placements once.
# Use AGENT_TIMEOUT_MULT=3.0 for slow models that hit the base task cap.
set -euo pipefail

method="${1:?method (poise|yamlonly|bk2)}"
agent="${2:?agent (codex|openclaw-flash|openclaw-pro|claude-code)}"
benchmark="${3:?benchmark (SI|SB)}"

cd "$(dirname "$0")/../.."   # release root

case "$method" in poise|yamlonly|bk2) ;; *) echo "ERR: unknown method $method"; exit 1;; esac
case "$benchmark" in SI|SB) ;; *) echo "ERR: unknown benchmark $benchmark"; exit 1;; esac
case "$agent" in codex|openclaw-flash|openclaw-pro|claude-code) ;;
    *) echo "ERR: unknown agent $agent"; exit 1;;
esac
if [ "$method" != "poise" ] && [ "$agent" != "codex" ]; then
    echo "ERR: paper protocol evaluates yamlonly/bk2 with codex only"; exit 1
fi

# Load env (DAYTONA_API_KEY, OPENAI_API_KEY, DEEPSEEK_API_KEY, ANTHROPIC_API_KEY)
source .envrc 2>/dev/null || { echo "ERR: .envrc missing; cp .envrc.template .envrc and fill keys"; exit 1; }

PYBIN="${PYBIN:-python}"
TAG="${benchmark}-${method}-${agent}"
sample="samples/${benchmark}/${method}.csv"
workdir="workdir/${TAG}"
cell_run_tag="${POISE_RUN_TAG:-${TAG}-$(date -u +%Y%m%dT%H%M%S)-$$}"
jobs_dir="jobs/${TAG}/${cell_run_tag}"
output="results/${TAG}/results.csv"
mkdir -p logs results "results/${TAG}" "$jobs_dir"
rm -f "logs/${TAG}_DONE"

if [ ! -f "$sample" ]; then
    echo "ERR: missing $sample. Run scripts/build_all_samples.sh first."; exit 1
fi

# Make the release-root vendored adapter importable by Harbor.
export PYTHONPATH="$PWD${PYTHONPATH:+:$PYTHONPATH}"

# Map agent name → Harbor agent/import path + model + version pin.
agent_import_args=()
case "$agent" in
    codex)
        agent_name=codex; model="openai/gpt-5.2"
        codex_version="${CODEX_VERSION_PIN:-0.131.0}"
        agent_kwargs=(--agent-kwarg "version=${codex_version}")
        max_workers=50
        ;;
    openclaw-flash)
        agent_name=openclaw; model="deepseek-v4-flash"
        : "${OPENCLAW_VERSION_PIN:?set OPENCLAW_VERSION_PIN=2026.4.15}"
        [[ "$OPENCLAW_VERSION_PIN" = "2026.4.15" ]] || {
            echo "ERR: paper environment requires OPENCLAW_VERSION_PIN=2026.4.15"; exit 1;
        }
        # The adapter hard-pins npm openclaw@2026.4.15 and lets Harbor record
        # the CLI's detected full version string, including build 041266a.
        agent_kwargs=()
        agent_import_args=(--agent-import-path "poise_harbor.openclaw:OpenClaw")
        export OPENCLAW_PROVIDER_BASE_URL="${OPENCLAW_PROVIDER_BASE_URL:-https://api.deepseek.com/v1}"
        export OPENCLAW_PROVIDER_API_KEY="${OPENCLAW_PROVIDER_API_KEY:-$DEEPSEEK_API_KEY}"
        export OPENCLAW_GATEWAY_TOKEN="harbor-$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        max_workers=80
        ;;
    openclaw-pro)
        agent_name=openclaw; model="deepseek-v4-pro"
        : "${OPENCLAW_VERSION_PIN:?set OPENCLAW_VERSION_PIN=2026.4.15}"
        [[ "$OPENCLAW_VERSION_PIN" = "2026.4.15" ]] || {
            echo "ERR: paper environment requires OPENCLAW_VERSION_PIN=2026.4.15"; exit 1;
        }
        agent_kwargs=()
        agent_import_args=(--agent-import-path "poise_harbor.openclaw:OpenClaw")
        export OPENCLAW_PROVIDER_BASE_URL="${OPENCLAW_PROVIDER_BASE_URL:-https://api.deepseek.com/v1}"
        export OPENCLAW_PROVIDER_API_KEY="${OPENCLAW_PROVIDER_API_KEY:-$DEEPSEEK_API_KEY}"
        export OPENCLAW_GATEWAY_TOKEN="harbor-$(head -c6 /dev/urandom | od -An -tx1 | tr -d ' \n')"
        max_workers=80
        # ds-v4-pro is ~60% slower than flash; bump agent timeout to 3x default.
        AGENT_TIMEOUT_MULT="${AGENT_TIMEOUT_MULT:-3.0}"
        ;;
    claude-code)
        agent_name=claude-code; model="claude-sonnet-4-6"
        # Pin claude-code CLI version to one known to work natively with Anthropic
        # /v1/messages (some relays silently downgrade newer versions).
        cc_version="${CLAUDE_CODE_VERSION_PIN:-2.1.146}"
        agent_kwargs=(--agent-kwarg "version=${cc_version}")
        # claude-code reads ANTHROPIC_API_KEY (or ANTHROPIC_AUTH_TOKEN) from env.
        # ANTHROPIC_BASE_URL defaults to https://api.anthropic.com.
        [ -z "${ANTHROPIC_API_KEY:-}" ] && { echo "ERR: ANTHROPIC_API_KEY unset; cc requires direct Anthropic API"; exit 1; }
        export CLAUDE_CODE_MAX_TURNS="${CLAUDE_CODE_MAX_TURNS:-120}"
        max_workers=40
        ;;
    *) echo "ERR: unknown agent $agent"; exit 1 ;;
esac

# Choose harness: vanilla for everything (the matrix doesn't use naivefix or amp).
HARNESS=src/run_docker_eval.py
control_args=()
[ "$agent" = "codex" ] && control_args+=(--read-coverage-control)

# Pick a SkillsBench tasks dir vs Skill-Inject. Override via *_TASKS env vars.
case "$benchmark" in
    SB) TASKS_DIR="${SB_TASKS_DIR:?set SB_TASKS_DIR to .../skillsbench/tasks}";;
    SI) TASKS_DIR="${SI_TASKS_DIR:?set SI_TASKS_DIR to .../skillinject_tasks}";;
esac

echo "=== $(date +%H:%M:%S)  prepare $TAG ==="
"$PYBIN" "$HARNESS" --sample "$sample" \
    --tasks-source "$TASKS_DIR" --workdir "$workdir" --prepare-only \
    "${control_args[@]}" \
    > "logs/${TAG}-prepare.log" 2>&1
tail -3 "logs/${TAG}-prepare.log"

# Number of trials per variant
case "$method" in
    poise|yamlonly) n_trials=2 ;;
    bk2)            n_trials=1 ;;  # k=2 random placements already give 2 attempts
    *) echo "ERR: unknown method $method"; exit 1 ;;
esac

for trial in $(seq 1 $n_trials); do
    trial_run_tag="${cell_run_tag}-trial${trial}"
    echo "=== $(date +%H:%M:%S)  $TAG  trial $trial/$n_trials  ($agent_name + $model) ==="
    "$PYBIN" src/run_throttled.py \
        --sample "$sample" \
        --workdir "$workdir" \
        --jobs-dir "$jobs_dir/trial${trial}" \
        --run-tag "$trial_run_tag" \
        --output "$output" \
        --limit-cpu 250 --limit-mem 500 --limit-disk 2000 \
        --max-workers "$max_workers" \
        --agent "$agent_name" --model "$model" --env daytona \
        "${agent_import_args[@]}" \
        "${agent_kwargs[@]}" \
        ${AGENT_TIMEOUT_MULT:+--agent-timeout-multiplier "$AGENT_TIMEOUT_MULT"} 2>&1 \
        | tee "logs/${TAG}-trial${trial}.log" | tail -10
done

echo "=== $(date +%H:%M:%S)  harvest $TAG ==="
rm -f "$output"
"$PYBIN" -c "
import sys; sys.path.insert(0, 'src')
from run_docker_eval import harvest_results, validate_harvested_results
from pathlib import Path
harvest_results(
    Path('$jobs_dir'), Path('$sample'), Path('$output'),
    agent_label='$agent', verifier_pass_threshold=1.0,
)
summary = validate_harvested_results(
    Path('$sample'), Path('$output'), attempts_per_variant=$n_trials,
)
print(
    'Validated harvest: '
    f\"{summary['variants']} variants, {summary['attempts']} attempts, \"
    f\"{summary['infrastructure_errors']} infrastructure errors\"
)
"

touch "logs/${TAG}_DONE"
echo "=== $(date +%H:%M:%S)  $TAG done ==="
