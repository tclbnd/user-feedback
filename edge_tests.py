#!/usr/bin/env python3
"""边界与输出结构测试 (非验收主流程, 可删除)。"""
import json
import abr

# 1) 无场景切换: 纯固定周期 GOP, 片尾余量收尾
r1 = abr.run({"frames": [{"t": i, "scene": 0} for i in range(20)],
              "ladder": [{"name": "a", "rate": 1000}], "gop": 6})
b = [g["idr"] for g in r1["gops"]]
print("no-cut gop boundaries:", b, "tail frames:", r1["gops"][-1]["frames"])
assert b == [0, 6, 12, 18]

# 2) gop=1: 每帧都是 IDR
r2 = abr.run({"frames": [{"t": i, "scene": i % 3} for i in range(5)],
              "ladder": [{"name": "a", "rate": 1000}], "gop": 1})
assert all(g["frames"] == 1 for g in r2["gops"]) and len(r2["gops"]) == 5
print("gop=1 ->", len(r2["gops"]), "single-frame IDR GOPs")

# 3) 非连续 t: 时间戳沿用源 t, 跨档一致且严格单调
ft = [{"t": t, "scene": 0} for t in range(0, 30, 3)]
r3 = abr.run({"frames": ft,
              "ladder": [{"name": "hi", "rate": 2000},
                         {"name": "lo", "rate": 500}], "gop": 4})
ts_hi = [f["pts"] for f in r3["ladder"]["hi"]["frames"]]
ts_lo = [f["pts"] for f in r3["ladder"]["lo"]["frames"]]
print("non-contiguous pts:", ts_hi)
assert ts_hi == ts_lo == [f["t"] for f in ft]
assert r3["alignment"]["aligned"]

# 4) 自定义 complexity 字段驱动帧级 QP
r4 = abr.run({"frames": [{"t": 0, "scene": 0, "complexity": 3.0},
                         {"t": 1, "scene": 0, "complexity": 1.0}],
              "ladder": [{"name": "a", "rate": 1000}], "gop": 2})
print("complexity-driven qp:", [f["qp"] for f in r4["ladder"]["a"]["frames"]])

# 5) 异常输入
try:
    abr.run({"frames": [], "ladder": [], "gop": 8})
    raise AssertionError("should raise")
except ValueError:
    print("empty ladder rejected OK")
try:
    abr.run({"frames": [{"t": 0, "scene": 0}],
             "ladder": [{"name": "a", "rate": 1}], "gop": 0})
    raise AssertionError("should raise")
except ValueError:
    print("gop<=0 rejected OK")
try:
    abr.run({"frames": [{"t": 0, "scene": 0}],
             "ladder": [{"name": "a", "rate": 1},
                        {"name": "a", "rate": 2}], "gop": 4})
    raise AssertionError("should raise")
except ValueError:
    print("duplicate rung names rejected OK")

# 输出结构样例
d = json.load(open("output.json"))
print("\ntop-level keys:", list(d.keys()))
print("gops[0:3]:", json.dumps(d["gops"][:3], ensure_ascii=False))
print("gop_stats sample:",
      json.dumps(d["ladder"]["rung_800"]["gop_stats"][1], ensure_ascii=False))
print("frame sample:",
      json.dumps(d["ladder"]["rung_800"]["frames"][8], ensure_ascii=False))
print("alignment checks:",
      [(c["name"], c["pass"]) for c in d["alignment"]["checks"]])
print("\nALL EDGE TESTS PASSED")
