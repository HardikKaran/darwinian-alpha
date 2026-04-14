#!/usr/bin/env bash
# quick_test.sh — Syntax-check submission.py and run a smoke test.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUBMISSION="$ROOT/submission.py"

echo "Building submission..."
python "$ROOT/tools/build_submission.py"

echo "Syntax check..."
python -m py_compile "$SUBMISSION" && echo "OK"

echo "Import check..."
python -c "import importlib.util, sys
spec = importlib.util.spec_from_file_location('submission', '$SUBMISSION')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
t = mod.Trader()
print('Trader instantiated OK')
"

echo "All checks passed."
