#!/usr/bin/env python3
"""Run one sync attempt under a crash-injection spec (used by test_faults).

CRASH_SPEC controls where this attempt dies:
  {"point": "pre",  "write_index": N}   -> die just BEFORE the Nth file write
  {"point": "post", "write_index": N}   -> die just AFTER  the Nth file write
  {"point": "pre",  "receive_index": N} -> die just before applying message N
  {"point": "post", "receive_index": N} -> die just after  applying message N
"""
import json
import os
import sys

WORK = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, WORK)

import apply  # noqa: E402
from publisher import ConnectionLost  # noqa: E402
from subscriber import sync  # noqa: E402

spec = json.loads(os.environ.get("CRASH_SPEC", "{}"))
counts = {"write": 0, "receive": 0}


def _wrap_write(orig):
    def inner(path, value):
        counts["write"] += 1
        hit = spec.get("write_index") == counts["write"]
        if hit and spec.get("point") == "pre":
            os._exit(9)
        result = orig(path, value)
        if hit:
            os._exit(9)
        return result
    return inner


apply.write_json = _wrap_write(apply.write_json)
apply.write_lines = _wrap_write(apply.write_lines)

_wrapped_receive = apply.Applier.receive


def _receive(self, m):
    counts["receive"] += 1
    hit = spec.get("receive_index") == counts["receive"]
    if hit and spec.get("point") == "pre":
        os._exit(9)
    done = _wrapped_receive(self, m)
    if hit:
        os._exit(9)
    return done


apply.Applier.receive = _receive


def _arg(value):
    return None if value in (None, "", "None") else int(value)


replica, publisher = sys.argv[1], sys.argv[2]
drop_after = _arg(sys.argv[3]) if len(sys.argv) > 3 else None
kill_after = _arg(sys.argv[4]) if len(sys.argv) > 4 else None

status, result = 0, None
try:
    result = sync(replica, publisher, drop_after, kill_after)
except ConnectionLost as exc:
    print(f"sync: {exc}", file=sys.stderr)
    status = 75
print(json.dumps({"result": result, "writes": counts["write"],
                  "receives": counts["receive"]}))
sys.exit(status)
