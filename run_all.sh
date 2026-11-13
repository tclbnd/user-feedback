#!/bin/sh
# Full acceptance run: static lineage vs annotations, incremental index,
# dynamic validation against independent SQLite execution.
set -e
cd "$(dirname "$0")"
mkdir -p out
python3 acceptance_static.py
python3 acceptance_index.py
python3 acceptance_dynamic.py
echo "ALL ACCEPTANCE SUITES PASSED"
