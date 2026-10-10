#!/usr/bin/env bash
# One-command full matrix runner for the Agent Overspend Benchmark.
#
# DO NOT run this at full scale before the Sun 2026-10-11 sandbox deploy lands --
# today's scope is the smoke run only (see ../SMOKE.md and smoke_run.sh). This script
# is prepared now so the full run is a single command once the deploy is confirmed.
#
# Usage:
#   GEMINI_API_KEY=... REPS=2 ./run_all.sh
#
# Env vars:
#   REPS                 reps per (model, prompt) pair, default 2 (see METHOD.md)
#   MODELS               space-separated list, default "claude gemini" (add "openai"
#                         once an openai_run.py exists and OPENAI_API_KEY has credit)
#   CLAUDE_MODEL          default "sonnet"
#   GEMINI_MODEL          default "gemini-3.8-flash"
#   OVERSPEND_MAX_CALLS   tool-call ceiling per Gemini run, default 15
#   OVERSPEND_MAX_TURNS   turn ceiling per Claude run, default 10
#
# Requires: GEMINI_API_KEY in env for the Gemini leg; `claude` CLI on PATH, logged in,
# for the Claude leg. Never prints key values.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="$HERE/../results"
mkdir -p "$OUT_DIR"
RUNS_FILE="$OUT_DIR/runs.jsonl"

REPS="${REPS:-2}"
MODELS="${MODELS:-claude gemini}"
CLAUDE_MODEL="${CLAUDE_MODEL:-sonnet}"
GEMINI_MODEL="${GEMINI_MODEL:-gemini-3.8-flash}"

PROMPT_IDS=$(python3 -c "import json; print(' '.join(p['id'] for p in json.load(open('$HERE/../prompts.json'))['prompts']))")

echo "Models: $MODELS | Reps: $REPS | Prompts: $(echo $PROMPT_IDS | wc -w)"

for model in $MODELS; do
  for pid in $PROMPT_IDS; do
    for rep in $(seq 0 $((REPS - 1))); do
      case "$model" in
        claude)
          echo "[claude] $pid rep$rep"
          python3 "$HERE/claude_run.py" "$pid" "$rep" "$CLAUDE_MODEL" >> "$RUNS_FILE"
          ;;
        gemini)
          if [ -z "${GEMINI_API_KEY:-}" ]; then
            echo "SKIP gemini: GEMINI_API_KEY not set" >&2
            continue
          fi
          echo "[gemini] $pid rep$rep"
          python3 "$HERE/gemini_run.py" "$pid" "$rep" "$GEMINI_MODEL" >> "$RUNS_FILE"
          sleep 5
          ;;
        openai)
          echo "SKIP openai: harness leg not implemented yet (see METHOD.md)" >&2
          ;;
        *)
          echo "Unknown model '$model' in MODELS" >&2
          ;;
      esac
    done
  done
done

python3 "$HERE/summarize.py" "$RUNS_FILE" "$OUT_DIR/summary.csv"
echo "Done. Raw: $RUNS_FILE  Summary: $OUT_DIR/summary.csv"
