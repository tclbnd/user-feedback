#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mvcc.py 的验收脚本。

覆盖三类检查:
  1. 交错脚本的可见性判定必须与"串行化参考实现"逐条一致。
     参考实现把每个 read 单独放到一个串行时间线上: 只重放该事务开始前
     已提交的写入 + 自己此前的写入, 与 MVCC 快照语义等价。
  2. 长对账会话(一直不 commit/end)期间, 多次 gc 的水位必须钉在该会话的
     begin_ts 上, 不得回收它仍可见的版本, 且并行会话不读到脏数据。
  3. 3 组交错序列分别复跑两次(含进程级复跑), 存储终态与统计摘要一致。

用法: python3 verify.py
"""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile

import mvcc


# --------------------------------------------------------------------------
# 串行化参考实现: 每条 read 独立求值
# --------------------------------------------------------------------------

def reference_read(ops, upto, txid, key):
    """串行等价语义下 (txid,key) 在 ops[upto] 处应当读到的值。

    构造: 按提交顺序重放, 但只接受 commit 落在"读事务 begin 之前"的事务;
    读事务自己的写入直接覆盖。
    """
    begin_pos = None
    for i in range(upto + 1):
        if ops[i].get("op") == "begin" and ops[i].get("tx") == txid:
            begin_pos = i
    assert begin_pos is not None, "read 前没有 begin"

    # 该事务自己的写入, 只取 read 之前最后一次写。
    own = None
    own_found = False
    for i in range(begin_pos, upto):
        op = ops[i]
        if op.get("op") == "write" and op.get("tx") == txid and op.get("k") == key:
            own, own_found = op.get("v"), True
    if own_found:
        return own

    # 事务 begin 之前已完成提交的写入, 按 commit 顺序取最终值。
    committed = {}
    open_writes = {}
    committed_before = set()
    for i in range(begin_pos):
        op = ops[i]
        kind = op.get("op")
        if kind == "begin":
            open_writes.setdefault(op.get("tx"), {})
        elif kind == "write":
            open_writes.setdefault(op.get("tx"), {})[op.get("k")] = op.get("v")
        elif kind == "commit":
            t = op.get("tx")
            for k, v in open_writes.get(t, {}).items():
                committed[k] = v
            committed_before.add(t)
            open_writes.pop(t, None)

    # 读之前同一事务不能有其它事务"已开始未提交"的写入生效 —— 参考实现
    # 天然丢弃它们(只收集 commit 事件), 这正是"不读脏数据"。
    return committed.get(key)


def check_visibility_matches_reference(ops, result):
    """逐条比对 visibility 与串行化参考。"""
    errors = []
    read_positions = [i for i, op in enumerate(ops) if op.get("op") == "read"]
    for vis in result["visibility"]:
        op_pos = read_positions[vis["read_index"]]
        expected = reference_read(ops, op_pos, vis["tx"], vis["k"])
        if vis["v"] != expected:
            errors.append((vis, expected))
    return errors


# --------------------------------------------------------------------------
# 交错脚本
# --------------------------------------------------------------------------

# 组 1: 读己之写 + 未提交写不可见 + 快照读(不读更晚提交)
SCHEDULE_1 = {"ops": [
    {"tx": 1, "op": "begin"},
    {"tx": 1, "op": "write", "k": "a", "v": 1},
    {"tx": 2, "op": "begin"},
    {"tx": 2, "op": "read", "k": "a"},          # 未提交 -> null
    {"tx": 1, "op": "commit"},
    {"tx": 2, "op": "read", "k": "a"},          # 快照早于 commit -> 仍 null
    {"tx": 2, "op": "write", "k": "a", "v": 2},
    {"tx": 2, "op": "read", "k": "a"},          # 读己之写 -> 2
    {"tx": 2, "op": "commit"},
    {"tx": 3, "op": "begin"},
    {"tx": 3, "op": "read", "k": "a"},          # 看到 tx2 的 2
    {"tx": 3, "op": "end"},
    {"op": "gc"},
]}

# 组 2: 长对账会话横跨多次版本更新, 期间 gc 水位钉住, 结束水位推进
SCHEDULE_2 = {"ops": [
    {"tx": 10, "op": "begin"},                       # 长对账会话
    {"tx": 10, "op": "read", "k": "balance"},        # null
    {"tx": 11, "op": "begin"},
    {"tx": 11, "op": "write", "k": "balance", "v": 100},
    {"tx": 11, "op": "commit"},
    {"tx": 10, "op": "read", "k": "balance"},        # 快照仍 null
    {"tx": 12, "op": "begin"},
    {"tx": 12, "op": "write", "k": "balance", "v": 200},
    {"tx": 12, "op": "commit"},
    {"op": "gc"},                                    # 水位=tx10 begin, 回收 0
    {"tx": 10, "op": "read", "k": "balance"},        # 仍 null (不读脏)
    {"tx": 13, "op": "begin"},
    {"tx": 13, "op": "read", "k": "balance"},        # 200
    {"tx": 13, "op": "end"},
    {"tx": 14, "op": "begin"},
    {"tx": 14, "op": "write", "k": "balance", "v": 300},
    {"tx": 14, "op": "commit"},
    {"op": "gc"},                                    # 水位仍=tx10 begin -> 回收 0
    {"tx": 10, "op": "end"},                         # 对账会话结束
    {"op": "gc"},                                    # 水位前进, 回收 100/200
    {"tx": 15, "op": "begin"},
    {"tx": 15, "op": "read", "k": "balance"},        # 300
    {"tx": 15, "op": "end"},
]}

# 组 3: 多键 + 并行会话交叉, 含回滚(end 未提交)与多键原子提交
SCHEDULE_3 = {"ops": [
    {"tx": 20, "op": "begin"},
    {"tx": 21, "op": "begin"},
    {"tx": 20, "op": "write", "k": "x", "v": 1},
    {"tx": 21, "op": "write", "k": "y", "v": 9},
    {"tx": 21, "op": "end"},                          # 回滚, y 不入链
    {"tx": 20, "op": "write", "k": "y", "v": 2},
    {"tx": 20, "op": "commit"},                       # x=1,y=2 原子可见
    {"tx": 22, "op": "begin"},
    {"tx": 22, "op": "read", "k": "x"},               # 1
    {"tx": 22, "op": "read", "k": "y"},               # 2
    {"tx": 22, "op": "write", "k": "x", "v": 3},
    {"tx": 22, "op": "read", "k": "x"},               # 读己之写 3
    {"tx": 23, "op": "begin"},
    {"tx": 23, "op": "read", "k": "x"},               # 快照 1
    {"tx": 22, "op": "commit"},
    {"op": "gc"},                                     # 水位=tx23 begin, 回收 0
    {"tx": 23, "op": "read", "k": "x"},               # 仍 1
    {"tx": 23, "op": "commit"},
    {"op": "gc"},                                     # 无活跃, 回收 x=1
    {"tx": 24, "op": "begin"},
    {"tx": 24, "op": "read", "k": "x"},               # 3
    {"tx": 24, "op": "read", "k": "y"},               # 2
    {"tx": 24, "op": "end"},
]}

SCHEDULES = [("s1_own_write", SCHEDULE_1),
             ("s2_long_reconciler", SCHEDULE_2),
             ("s3_parallel_multi_key", SCHEDULE_3)]


# --------------------------------------------------------------------------
# 检查项
# --------------------------------------------------------------------------

def check_no_dirty_reads(ops, result):
    """任何读都不能看到未提交事务的写入。"""
    errors = []
    # 用 visibility 自带的 source 判断: 只要不是 own_write, 值必须等于某个
    # 已提交版本的值。
    committed_values = set()
    open_writes = {}
    for i, op in enumerate(ops):
        kind = op.get("op")
        if kind == "write":
            open_writes.setdefault(op.get("tx"), {})[op.get("k")] = op.get("v")
        elif kind == "commit":
            for k, v in open_writes.pop(op.get("tx"), {}).items():
                committed_values.add((k, json.dumps(v)))
        elif kind == "end":
            open_writes.pop(op.get("tx"), None)
    for vis in result["visibility"]:
        if vis["v"] is None:
            continue
        if (vis["k"], json.dumps(vis["v"])) in committed_values:
            continue
        if vis["source"] == "own_write":
            continue
        errors.append(vis)
    return errors


def check_gc_watermark(ops, result):
    """gc 不得回收任何活跃快照仍可见的版本。"""
    errors = []
    # 重放以判断每次 gc 保留的版本是否覆盖最老活跃快照的可读需求。
    versions = {}
    active = {}
    committed_ok = {}  # (k,commit_ts) 已提交
    clock = 0
    gc_event_idx = 0
    for i, op in enumerate(ops):
        kind = op.get("op")
        if kind == "begin":
            clock += 1
            active[op.get("tx")] = clock
        elif kind == "write":
            pass
        elif kind == "commit":
            clock += 1
            committed_ok.setdefault(op.get("tx"), clock)
            active.pop(op.get("tx"), None)
        elif kind == "end":
            active.pop(op.get("tx"), None)
        elif kind == "gc":
            event = result["gc_log"][gc_event_idx]
            gc_event_idx += 1
            if active:
                live = sorted(active.values())
                if event["watermark"] != live[0]:
                    errors.append(("watermark", i, event["watermark"], live[0]))
                # 断言: 回收的每个版本 commit_ts 都 < 水位
                for rv in event["reclaimed_versions"]:
                    if rv["commit_ts"] >= live[0]:
                        errors.append(("reclaimed-visible-version", i, rv, live[0]))
    return errors


def replay_result(ops):
    return mvcc.run_ops(copy.deepcopy(ops))


def strip_gc(ops):
    return [op for op in ops if op.get("op") != "gc"]


def check_gc_invisible(ops, result):
    """gc 不得改变任何读取结果: 与"从不 gc"的运行逐条比对。

    这条不变式同时覆盖"长对账会话期间不读脏数据"和"不回收快照可见版本":
    一旦回收了某个活跃快照仍需要的历史版本, 该快照的后续读就会退化。
    """
    no_gc = mvcc.run_ops(strip_gc(ops))
    return [(a, b) for a, b in zip(result["visibility"], no_gc["visibility"])
            if (a["tx"], a["k"], a["v"]) != (b["tx"], b["k"], b["v"])]


def check_replayable(result):
    """用输出中的 replay 轨迹重新执行, 必须得到完全一致的结果。"""
    again = mvcc.run_ops(copy.deepcopy(result["replay"]))
    diffs = []
    if again["versions"] != result["versions"]:
        diffs.append(("versions", again["versions"], result["versions"]))
    if again["reads"] != result["reads"]:
        diffs.append(("reads", again["reads"], result["reads"]))
    if again["gc_stats"] != result["gc_stats"]:
        diffs.append(("gc_stats", again["gc_stats"], result["gc_stats"]))
    if again["state_digest"] != result["state_digest"]:
        diffs.append(("state_digest", again["state_digest"], result["state_digest"]))
    return diffs


def check_baseline_retained():
    """水位之下的一个基准版本必须保留, 供最老快照读取。"""
    ops = [
        {"tx": 1, "op": "begin"},
        {"tx": 1, "op": "write", "k": "a", "v": "v1"},
        {"tx": 1, "op": "commit"},
        {"tx": 2, "op": "begin"},                 # 快照起点: 只能看到 v1
        {"tx": 3, "op": "begin"},
        {"tx": 3, "op": "write", "k": "a", "v": "v2"},
        {"tx": 3, "op": "commit"},
        {"op": "gc"},                             # 水位=tx2 begin, v1 必须保留
        {"tx": 2, "op": "read", "k": "a"},
        {"tx": 4, "op": "begin"},
        {"tx": 4, "op": "write", "k": "a", "v": "v3"},
        {"tx": 4, "op": "commit"},
        {"op": "gc"},                             # 水位不变, v1 仍需保留
        {"tx": 2, "op": "read", "k": "a"},
        {"tx": 2, "op": "end"},
        {"op": "gc"},                             # 可回收 v1、v2
        {"tx": 5, "op": "begin"},
        {"tx": 5, "op": "read", "k": "a"},
        {"tx": 5, "op": "end"},
    ]
    res = mvcc.run_ops(ops)
    errors = []
    values = [v["v"] for v in res["visibility"]]
    if values != ["v1", "v1", "v3"]:
        errors.append(("baseline", values))
    if res["gc_log"][0]["reclaimed"] != 0 or res["gc_log"][1]["reclaimed"] != 0:
        errors.append(("premature-reclaim", [e["reclaimed"] for e in res["gc_log"]]))
    # 时钟轨迹: begin1=1 commit1=2 begin2=3 begin3=4 commit3=5 begin4=6
    # commit4=7 -> 终态只剩 v3(commit_ts=7)。
    if res["versions"] != {"a": [[7, "v3"]]}:
        errors.append(("final-versions", res["versions"]))
    if res["gc_stats"]["reclaimed"] != 2:
        errors.append(("gc-stats", res["gc_stats"]))
    return errors


# --------------------------------------------------------------------------
# 随机交错压测: 与串行化参考比对, 并检查 gc 不变式
# --------------------------------------------------------------------------

def fuzz(seed, rounds=40):
    import random
    rnd = random.Random(seed)
    problems = []
    for _ in range(rounds):
        keys = ["a", "b", "c"][:rnd.randint(1, 3)]
        ops = []
        live = []       # 当前活跃 tx
        next_id = 1
        counter = 0
        for _ in range(rnd.randint(6, 30)):
            action = rnd.random()
            if not live or (action < 0.18 and len(live) < 4):
                tx = next_id
                next_id += 1
                live.append(tx)
                ops.append({"tx": tx, "op": "begin"})
            elif action < 0.42:
                tx = rnd.choice(live)
                counter += 1
                ops.append({"tx": tx, "op": "write",
                            "k": rnd.choice(keys), "v": counter})
            elif action < 0.72:
                ops.append({"tx": rnd.choice(live), "op": "read",
                            "k": rnd.choice(keys)})
            elif action < 0.84:
                tx = rnd.choice(live)
                live.remove(tx)
                ops.append({"tx": tx, "op": "commit"})
            elif action < 0.92:
                tx = rnd.choice(live)
                live.remove(tx)
                ops.append({"tx": tx, "op": "end"})
            else:
                ops.append({"op": "gc"})

        res = mvcc.run_ops(ops)
        for vis, expected in check_visibility_matches_reference(ops, res):
            problems.append((seed, "visibility", vis, expected, ops))
        for bad in check_gc_invisible(ops, res):
            problems.append((seed, "gc-invisible", bad, ops))
        for diff in check_replayable(res):
            problems.append((seed, "replay", diff, ops))
        for err in check_gc_watermark(ops, res):
            problems.append((seed, "watermark", err, ops))
    return problems


def run_cli(ops):
    base = os.path.join(os.getcwd(), ".verify_tmp")
    os.makedirs(base, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=base)
    try:
        inp = os.path.join(tmp, "in.json")
        out = os.path.join(tmp, "out.json")
        with open(inp, "w", encoding="utf-8") as f:
            json.dump({"ops": ops}, f)
        subprocess.run([sys.executable, "mvcc.py", inp, out], check=True)
        with open(out, "r", encoding="utf-8") as f:
            return json.load(f)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    failures = 0
    for name, sched in SCHEDULES:
        ops = sched["ops"]
        r1 = mvcc.run_ops(copy.deepcopy(ops))
        r2 = mvcc.run_ops(copy.deepcopy(ops))
        r3 = run_cli(ops)  # 进程级复跑

        ok = True
        def fail(msg):
            nonlocal ok
            ok = False
            print("  FAIL [%s] %s" % (name, msg))

        if json.dumps(r1, sort_keys=True) != json.dumps(r2, sort_keys=True):
            fail("同进程复跑结果不一致")
        if json.dumps(r1["versions"], sort_keys=True) != json.dumps(r3["versions"], sort_keys=True):
            fail("进程级复跑 versions 不一致")
        if r1["gc_stats"] != r3["gc_stats"] or r1["digest"] != r3["digest"]:
            fail("进程级复跑统计/摘要不一致")
        if r1["state_digest"] != r3["state_digest"]:
            fail("进程级复跑终态摘要不一致")

        for vis, expected in check_visibility_matches_reference(ops, r1):
            fail("可见性判定与串行参考不一致: %r 期望 %r" % (vis, expected))
        for vis in check_no_dirty_reads(ops, r1):
            fail("脏读: %r" % (vis,))
        for err in check_gc_watermark(ops, r1):
            fail("回收水位错误: %r" % (err,))
        for bad in check_gc_invisible(ops, r1):
            fail("gc 改变了读取结果: %r" % (bad,))
        for diff in check_replayable(r1):
            fail("replay 轨迹不可重放: %r" % (diff,))

        reads = " ".join("%s:%s=%s" % (v["tx"], v["k"], v["v"]) for v in r1["visibility"])
        print("[%s] %s" % ("PASS" if ok else "FAIL", name))
        print("  reads: %s" % reads)
        print("  versions: %s" % json.dumps(r1["versions"], sort_keys=True))
        print("  gc: %s watermark=%s" % (r1["gc_stats"],
              [e["watermark"] for e in r1["gc_log"]]))
        print("  digest: %s state: %s" % (r1["digest"][:12], r1["state_digest"][:12]))
        if not ok:
            failures += 1

    # 三组序列的终态摘要互不相同, 说明没有把不同脚本压成同一结果。
    digests = []
    for name, sched in SCHEDULES:
        digests.append(mvcc.run_ops(copy.deepcopy(sched["ops"]))["state_digest"])
    if len(set(digests)) != len(digests):
        print("[FAIL] 三组交错序列终态摘要发生碰撞")
        failures += 1

    print("\n%s (%d/%d 组通过)" % ("ALL PASS" if failures == 0 else "FAILURES",
                                   len(SCHEDULES) - failures, len(SCHEDULES)))

    print("\n-- 长对账会话/基准版本保留 --")
    baseline_errors = check_baseline_retained()
    for err in baseline_errors:
        print("  FAIL %r" % (err,))
    if not baseline_errors:
        print("  PASS 水位之下保留基准版本, 对账会话读值不被 gc 影响")
    failures += len(baseline_errors)

    print("\n-- 随机交错压测 (5 seeds x 40 轮) --")
    problems = []
    for seed in range(5):
        problems.extend(fuzz(seed))
    if problems:
        for p in problems[:5]:
            print("  FAIL seed=%s kind=%s %r" % (p[0], p[1], p[2]))
        failures += len(problems)
    else:
        print("  PASS 200 轮随机交错: 可见性/回收/重放全部一致")

    print("\n%s" % ("ALL CHECKS PASS" if failures == 0 else "%d 项失败" % failures))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())