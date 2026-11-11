#!/usr/bin/env bash
#
# One-click local acceptance for the resumable streaming NDJSON pipeline.
#
#   1. unit + differential regression (streaming decode & normalization,
#      reference implementation, fixed rejection samples, replay matrix)
#   2. coverage feedback over the streaming/normalization modules
#   3. hard process kills at three phases + original-input replay, comparing
#      records / rejections / conflicts / checkpoint against a clean baseline
#   4. bounded-memory run on a source much larger than the read buffer,
#      reporting tracemalloc peak allocation
#
# Usage:  ./acceptance.sh
# Exit code is non-zero if any stage fails.

set -euo pipefail

cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3}"
WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/ndjson-acceptance.XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT

echo "=================================================================="
echo " stage 1/4  unit tests + differential regression"
echo "=================================================================="
"$PYTHON" -m unittest discover -s tests -v

echo
echo "=================================================================="
echo " stage 2/4  coverage feedback (legal corpus first, fixed rejects)"
echo "=================================================================="
"$PYTHON" tools/coverage_feedback.py

echo
echo "=================================================================="
echo " stage 3/4  crash at 3 phases + replay + state comparison"
echo "=================================================================="
"$PYTHON" -m ndjson_pipe.crash_acceptance \
  --file fixtures/supplier-a.ndjson \
  --source-id acceptance-fixture \
  --workdir "$WORKDIR/crash-fixture" \
  --chunk-size 3 --crash-line 2

# A second, richer source: mixed schema versions, unicode and fixed rejects.
"$PYTHON" - "$WORKDIR" <<'PY'
import os, sys
sys.path.insert(0, os.getcwd())
from tests.corpus import legal_ndjson, fixed_reject_ndjson
workdir = sys.argv[1]
data = legal_ndjson(seed=2026, n=30) + fixed_reject_ndjson() + legal_ndjson(seed=77, n=10)
with open(os.path.join(workdir, "mixed-source.ndjson"), "wb") as fh:
    fh.write(data)
PY
"$PYTHON" -m ndjson_pipe.crash_acceptance \
  --file "$WORKDIR/mixed-source.ndjson" \
  --source-id acceptance-mixed \
  --workdir "$WORKDIR/crash-mixed" \
  --chunk-size 5 --crash-line 6

echo
echo "=================================================================="
echo " stage 4/4  bounded memory + peak allocation report"
echo "=================================================================="
"$PYTHON" -m ndjson_pipe.memory_report "$WORKDIR"
echo
echo "ALL ACCEPTANCE STAGES PASSED"
echo "artifacts: $WORKDIR (removed on exit)"
