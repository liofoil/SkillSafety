#!/bin/bash
# Run the 12-cell paper matrix:
#   POISE x 4 agents x 2 benchmarks = 8
#   YAML  x codex    x 2 benchmarks = 2
#   B@k=2 x codex    x 2 benchmarks = 2
#
# Usage:
#   bash scripts/matrix/run_all.sh
#   bash scripts/matrix/run_all.sh --only=SB
#   bash scripts/matrix/run_all.sh --only=poise
#   bash scripts/matrix/run_all.sh --list

set -euo pipefail
cd "$(dirname "$0")/../.."

POISE_AGENTS=("codex" "openclaw-flash" "openclaw-pro" "claude-code")
ALL_BENCHES=("SI" "SB")
MATRIX_CELLS=()
for bench in "${ALL_BENCHES[@]}"; do
    for agent in "${POISE_AGENTS[@]}"; do
        MATRIX_CELLS+=("${bench}|poise|${agent}")
    done
    MATRIX_CELLS+=("${bench}|yamlonly|codex")
    MATRIX_CELLS+=("${bench}|bk2|codex")
done

only_filter=""
list_only=0
for arg in "$@"; do
    case "$arg" in
        --only=*) only_filter="${arg#--only=}" ;;
        --list) list_only=1 ;;
        *) echo "ERR: unknown argument $arg"; exit 1 ;;
    esac
done

want_cell() {
    local m="$1" a="$2" b="$3"
    [ -z "$only_filter" ] && return 0
    local f
    while IFS= read -r f; do
        if [ "$m" = "$f" ] || [ "$a" = "$f" ] || [ "$b" = "$f" ]; then
            return 0
        fi
    done < <(printf '%s\n' "$only_filter" | tr ',' '\n')
    return 1
}

skip_done() {
    local tag="$1"
    if [ -f "logs/${tag}_DONE" ]; then
        echo "  SKIP (DONE flag exists): $tag"
        return 0
    fi
    return 1
}

total=0
done_count=0
failed_count=0
for cell in "${MATRIX_CELLS[@]}"; do
    IFS='|' read -r bench method agent <<< "$cell"
    if ! want_cell "$method" "$agent" "$bench"; then
        continue
    fi
    total=$((total + 1))
    tag="${bench}-${method}-${agent}"

    if [ "$list_only" -eq 1 ]; then
        echo "$tag"
        continue
    fi
    if skip_done "$tag"; then
        done_count=$((done_count + 1))
        continue
    fi

    echo
    echo "============================================================"
    echo "  Cell: $tag    ($(date +%H:%M:%S))"
    echo "============================================================"
    if bash scripts/matrix/run_cell.sh "$method" "$agent" "$bench"; then
        done_count=$((done_count + 1))
    else
        failed_count=$((failed_count + 1))
        echo "  FAILED: $tag (continuing with next cell)"
    fi
done

if [ "$list_only" -eq 1 ]; then
    echo "TOTAL=$total"
    exit 0
fi

echo
echo "============================================================"
echo "  Matrix summary: $done_count / $total succeeded, $failed_count failed"
echo "============================================================"
echo
echo "Per-cell trials:  results/<tag>/results.csv"
echo "Aggregate with:   python src/aggregate_matrix.py --input results/ \\"
echo "                    --output-csv results/matrix_summary.csv \\"
echo "                    --output-json results/matrix_summary.json"
if [ "$failed_count" -gt 0 ]; then
    exit 1
fi
