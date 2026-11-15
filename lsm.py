#!/usr/bin/env python3
"""Entry point: ``python3 lsm.py ops.json output.json``.

The original defect-baseline implementation is preserved as
``baseline_buggy.py`` for contrast/regression tests.  This module wires the
correct layered compaction engine in :mod:`lake` to the documented CLI.
"""
import sys

from lake.cli import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
