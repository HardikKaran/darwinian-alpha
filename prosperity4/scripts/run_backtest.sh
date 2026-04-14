#!/usr/bin/env bash
# run_backtest.sh — Run the backtester against the current submission.py

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMISSION="$ROOT/submission.py"
LOG_DIR="$ROOT/backtests"
TIMESTAMP="$(date +%Y%m%d_%H%M%S)"
LOG_FILE="$LOG_DIR/run_$TIMESTAMP.log"

mkdir -p "$LOG_DIR"

echo "Building submission..."
python "$ROOT/tools/build_submission.py"

echo "Running backtest..."
python -m prosperity4bt "$SUBMISSION" "$@" 2>&1 | tee "$LOG_FILE"

echo "Log saved to $LOG_FILE"
