#!/bin/sh
# One-command local acceptance: unit + differential tests, then the
# crash/replay, conflict, versioning and memory acceptance run.
set -e
cd "$(dirname "$0")"
python3 -m unittest discover -s tests -v
python3 acceptance.py
