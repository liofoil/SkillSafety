#!/bin/bash
# Build the four context-free-reference sample CSVs (2 methods × 2 benchmarks).
#
# Prerequisites:
#   - SkillsBench tasks pool cloned (see README)  ── path in SB_TASKS_DIR
#   - Skill-Inject tasks pool cloned             ── path in SI_TASKS_DIR
#   - manifests/{skillinject,skillsbench}-tasks.txt
#
# Generates:
#   samples/SI/yamlonly.csv    (75 variants)
#   samples/SI/bk2.csv         (150 variants = 75 × 2 placements)
#   samples/SB/yamlonly.csv    (81 variants)
#   samples/SB/bk2.csv         (162 variants = 81 × 2 placements)
#
# POISE samples (our method) are NOT generated here — they require a
# context-aware LLM call per (task, harm) variant. The two reference
# generators below also call the configured model, but receive only the
# target command and no skill/position context. Generate POISE separately:
#   python src/poise_pipeline.py \
#       --manifest manifests/skillsbench-tasks.txt \
#       --task-pool $SB_TASKS_DIR \
#       --benchmark SB \
#       --runs-out runs/SB-poise-matrix/ \
#       --output samples/SB/poise.csv

set -euo pipefail
cd "$(dirname "$0")/.."

source .envrc 2>/dev/null || true

PYBIN="${PYBIN:-python}"
: "${SI_TASKS_DIR:?set SI_TASKS_DIR to your skillinject_tasks dir}"
: "${SB_TASKS_DIR:?set SB_TASKS_DIR to your skillsbench/tasks dir}"

# YAML-only references (context-free generator at the configured T=0.7)
$PYBIN src/build_matrix_samples.py \
    --manifest manifests/skillinject-tasks.txt \
    --task-pool "$SI_TASKS_DIR" \
    --benchmark SI --method yamlonly \
    --output samples/SI/yamlonly.csv \
    --runs-out runs/SI-yamlonly-matrix/

$PYBIN src/build_matrix_samples.py \
    --manifest manifests/skillsbench-tasks.txt \
    --task-pool "$SB_TASKS_DIR" \
    --benchmark SB --method yamlonly \
    --output samples/SB/yamlonly.csv \
    --runs-out runs/SB-yamlonly-matrix/

# B@k=2 references (one generated sentence, two distinct body placements)
$PYBIN src/build_matrix_samples.py \
    --manifest manifests/skillinject-tasks.txt \
    --task-pool "$SI_TASKS_DIR" \
    --benchmark SI --method bk2 \
    --output samples/SI/bk2.csv \
    --runs-out runs/SI-bk2-matrix/ \
    --bk2-placements 2 --seed 42

$PYBIN src/build_matrix_samples.py \
    --manifest manifests/skillsbench-tasks.txt \
    --task-pool "$SB_TASKS_DIR" \
    --benchmark SB --method bk2 \
    --output samples/SB/bk2.csv \
    --runs-out runs/SB-bk2-matrix/ \
    --bk2-placements 2 --seed 42

echo
echo "Done. Sample counts:"
for f in samples/SI/yamlonly.csv samples/SI/bk2.csv samples/SB/yamlonly.csv samples/SB/bk2.csv; do
    rows=$(($(wc -l < "$f") - 1))
    printf "  %-30s  %d rows\n" "$f" "$rows"
done

echo
echo "Next: scripts/matrix/run_all.sh  (runs the 12-cell paper matrix)"
echo "  Or to execute just one cell:   bash scripts/matrix/run_cell.sh yamlonly codex SB"
