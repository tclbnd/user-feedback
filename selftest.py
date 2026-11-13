#!/usr/bin/env python3
"""Acceptance self-test for pipeline.py.

Covers:
  A1. 10-tick downstream stall -> zero loss, in-order catch-up, no false
      deadlock, water levels fall back level by level.
  A2. concurrency 2/4/8 -> same throughput and inter-stage order guarantee,
      buffer occupancy never exceeds caps.
  A3. configurable, metered reject policy ("drop").
  A4. priority admission within an epoch, epochs never interleave.
  A5. CLI compatibility: python3 pipeline.py scenario.json output.json
"""
import json, os, subprocess, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pipeline import run

FAILURES = []

def check(name, cond, detail=""):
    tag = "PASS" if cond else "FAIL"
    print(f"[{tag}] {name}" + (f"  ({detail})" if detail else ""))
    if not cond:
        FAILURES.append(name)

def bounds_ok(sc, res):
    for s in sc["stages"]:
        lv = res["water_levels"][s["name"]]
        if lv and max(lv) > s["cap"]:
            return False
    return True

def final_levels_zero(res):
    return all(lv[-1] == 0 for lv in res["water_levels"].values())

BASE = {"stages": [{"name": "src", "cap": 8},
                   {"name": "mid", "cap": 4},
                   {"name": "snk", "cap": 4}],
        "items": 40, "stall": {"stage": "snk", "from": 10, "to": 20},
        "concurrency": 1, "seed": 1}

# ---- A1: 10s downstream stall, zero loss, ordered catch-up, no deadlock ----
res = run(dict(BASE))
check("A1 zero loss under 10-tick stall", res["dropped"] == 0)
check("A1 all items delivered", res["throughput"] == BASE["items"])
check("A1 in-order catch-up after recovery", res["order_ok"] and res["epoch_ok"])
check("A1 no false deadlock under stall", not res["deadlock"])
check("A1 backpressure reached every level",
      all(res["blocked_ticks"][n] > 0 for n in ("src", "mid")),
      str(res["blocked_ticks"]))
check("A1 waterlines never exceed caps; downstream levels saturate",
      bounds_ok(BASE, res)
      and res["high_water"]["snk"] == 4 and res["high_water"]["mid"] == 4
      and res["high_water"]["src"] > 0,
      str(res["high_water"]))
check("A1 water levels fall back to 0 after recovery", final_levels_zero(res))
# waterline protocol: backpressure fills snk first, then holds it at cap
snk_lv = res["water_levels"]["snk"]
check("A1 snk fills then stays pinned at cap through the stall",
      all(v == 4 for v in snk_lv[13:20]), str(snk_lv[10:20]))

# no-stall run: deadlock detector must not fire either
res_ns = run({**BASE, "stall": None})
check("A1 no false deadlock without stall",
      not res_ns["deadlock"] and res_ns["throughput"] == BASE["items"])

# ---- A2: concurrency 2/4/8 keeps throughput & ordering guarantees ----------
for c in (2, 4, 8):
    sc = {**BASE, "concurrency": c}
    r = run(sc)
    check(f"A2 c={c} throughput unchanged", r["throughput"] == BASE["items"])
    check(f"A2 c={c} order/epoch guarantee unchanged",
          r["order_ok"] and r["epoch_ok"])
    check(f"A2 c={c} zero loss, no deadlock",
          r["dropped"] == 0 and not r["deadlock"])
    check(f"A2 c={c} buffer occupancy within bounds", bounds_ok(sc, r))

# ---- A3: configurable, metered reject policy -------------------------------
sc = {**BASE, "concurrency": 4, "reject_policy": "drop"}
r = run(sc)
check("A3 drop policy meters drops", r["dropped"] > 0)
check("A3 drops attributed per stage",
      r["dropped"] == sum(r["drops_by_stage"].values())
      and r["drops_by_stage"]["src"] == r["dropped"])
check("A3 drop policy still terminates, no deadlock",
      not r["deadlock"] and r["ticks"] < 10000)
check("A3 surviving items stay in order", r["order_ok"] and r["epoch_ok"])

# ---- A4: priority admission within epoch, epochs never interleave ----------
prios = [(i * 7) % 3 for i in range(BASE["items"])]
sc = {**BASE, "concurrency": 3, "priorities": prios}
r = run(sc)
check("A4 priority admission keeps pipeline order (vs admission order)",
      r["order_ok"])
check("A4 same-epoch data never crosses epochs", r["epoch_ok"])
check("A4 zero loss with priorities", r["dropped"] == 0
      and r["throughput"] == BASE["items"])

# ---- A5: CLI compatibility --------------------------------------------------
tmp = tempfile.mkdtemp()
sc_path = os.path.join(tmp, "scenario.json")
out_path = os.path.join(tmp, "output.json")
with open(sc_path, "w") as f:
    json.dump(BASE, f)
subprocess.run([sys.executable, "pipeline.py", sc_path, out_path],
               cwd=os.path.dirname(os.path.abspath(__file__)), check=True)
out = json.load(open(out_path))
need = {"dropped", "deadlock", "water_levels", "blocked_ticks",
        "throughput", "order_ok", "ticks"}
check("A5 CLI output has all contract keys", need <= set(out))
check("A5 CLI result matches library result",
      out["throughput"] == res["throughput"] and out["ticks"] == res["ticks"]
      and out["water_levels"] == res["water_levels"])

print()
if FAILURES:
    print(f"{len(FAILURES)} FAILURES: {FAILURES}")
    sys.exit(1)
print("ALL CHECKS PASSED")
