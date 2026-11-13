#!/bin/sh
# Thin wrapper: the transcript itself is driven by cli_session.py because the
# sandbox denies the mktemp binary but allows Python's tempfile.
cd "$(dirname "$0")" || exit 1
PYTHONPYCACHEPREFIX=/tmp/pyc exec python3 cli_session.py
