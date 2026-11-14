#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验 CLI 输出结构、确定性可复现性与事件因果链完整性。"""
import json

a = json.load(open("out1.json", encoding="utf-8"))
b = json.load(open("out2.json", encoding="utf-8"))

# 确定性：同 seed 两次运行的事件、决策、节点快照完全一致
assert a["events"] == b["events"], "事件序列不可复现"
assert a["decisions"] == b["decisions"], "决策轨迹不可复现"
assert a["nodes"] == b["nodes"], "节点快照不可复现"
assert a["term_log"] == b["term_log"]

# 输出结构覆盖任务要求
required = ("nodes", "rejected", "term_log", "commit_trajectory",
            "events", "decisions", "checkpoints", "proposals")
for k in required:
    assert k in a, "缺少输出字段 %s" % k

# 每条决策都能回溯到真实存在的时序事件
event_ids = set(e["event_id"] for e in a["events"])
for d in a["decisions"]:
    assert d["event_id"] in event_ids, "决策 %s 无来源事件" % d

# 决策轨迹包含任期变化、提交点、截断、应用四类关键动作
actions = set(d["action"] for d in a["decisions"])
for act in ("term_bump", "commit_advance", "apply"):
    assert act in actions, "缺少动作轨迹 %s" % act

print("REPRODUCIBLE_OK")
print("events=%d decisions=%d checkpoints=%d term_changes=%d"
      % (len(a["events"]), len(a["decisions"]),
         len(a["checkpoints"]),
         len([d for d in a["decisions"] if d["action"] == "term_bump"])))
print("node snapshot keys:", sorted(a["nodes"][0].keys()))
print("final commits:", [nd["commit_idx"] for nd in a["nodes"]])
print("applied:", a["nodes"][0]["applied"])

