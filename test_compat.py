#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""确认 README 原始最小 req schema(仅 level_db/flat/min_ms)仍被支持。"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
req_path = os.path.join(DATA, "min.req.json")
out_path = os.path.join(DATA, "min.out.json")
json.dump({"pcm": "inject.wav", "fs": 16000,
           "features": {"level_db": -45, "flat": 0.6, "min_ms": 200}},
          open(req_path, "w"))
subprocess.check_call([sys.executable, os.path.join(HERE, "cut.py"), req_path, out_path])
d = json.load(open(out_path))
assert d["keep"] and d["drop"] and d["boundaries"] and d["segments"]
# 缺省参数自动补齐
assert d["config"]["guard_ms"] == 50 and d["config"]["merge_ms"] == 300
print("minimal schema keep:", [(k["start"], k["end"]) for k in d["keep"]])
print("defaults backfilled, boundaries:", d["boundaries"])
os.remove(req_path)
os.remove(out_path)
print("COMPAT PASS")
