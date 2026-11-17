#!/usr/bin/env python3
"""Run one aggregation query."""
import json
import sys

from aggregate import run

print(json.dumps(run(json.load(open(sys.argv[1], encoding="utf-8")))))
