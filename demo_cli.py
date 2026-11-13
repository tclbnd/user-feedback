#!/usr/bin/env python3
"""Run the README scenario through the CLI and print a summary."""
import json, os, subprocess, sys, tempfile

sc = {"stages": [{"name": "src", "cap": 8},
                 {"name": "mid", "cap": 4},
                 {"name": "snk", "cap": 4}],
      "items": 40, "stall": {"stage": "snk", "from": 10, "to": 20},
      "concurrency": 1, "seed": 1}

tmp = tempfile.mkdtemp()
sp, op = os.path.join(tmp, "scenario.json"), os.path.join(tmp, "output.json")
with open(sp, "w") as f:
    json.dump(sc, f)
subprocess.run([sys.executable, "pipeline.py", sp, op], check=True)
r = json.load(open(op))
print({k: v for k, v in r.items() if k != "water_levels"})
for name, lv in r["water_levels"].items():
    print(f"{name} water: {lv}")
