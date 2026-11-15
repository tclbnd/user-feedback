#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
屏障一致性验收测试(纯标准库,无需 pytest):

  python3 tests/test_barrier.py

覆盖:
  G1 分片失联 + release 策略(released + 滚动到下一批,无挂死)
  G2 分片失联/重启 + fail 策略(整批 fail,重启迟到分片滚入下一批 pass)
  G3 乱序到达 + 多批次连续故障(released,迟到滚入最终批)
  另含健康基线、pending 语义、fail/release 策略对照、50 轮随机属性测试。

每组故障序列以三种投递变体复跑:
  in_order : 规范化顺序
  shuffled : 同事件集合随机乱序
  retry    : 乱序 + 重复消息(模拟重试)
要求三种变体的 digest、批次结果、逐分片决策完全一致。
"""
import json
import os
import random
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from barrier import simulate  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


# --------------------------- 三组故障注入序列 ---------------------------

def group1_lost_release():
    # 4 分片 / 2 批:分片 2 在批次1失联,30 拍超时 -> release 放行;
    # 分片 2 重启后的迟到 arrive 滚入批次2,批次2 最终凑齐 pass。
    return dict(
        shards=4, timeout=30, policy="release", batches=2,
        events=[
            {"t": 0, "shard": 0, "batch": 1, "type": "arrive"},
            {"t": 1, "shard": 1, "batch": 1, "type": "arrive"},
            {"t": 2, "shard": 3, "batch": 1, "type": "arrive"},
            {"t": 5, "shard": 2, "batch": 1, "type": "lost"},
            {"t": 30, "shard": 2, "batch": 1, "type": "timeout"},
            # 批次2 的正常到达
            {"t": 31, "shard": 0, "batch": 2, "type": "arrive"},
            {"t": 32, "shard": 1, "batch": 2, "type": "arrive"},
            {"t": 33, "shard": 3, "batch": 2, "type": "arrive"},
            # 分片2 重启:其批次1 的迟到到达不阻塞批次1,滚入批次2
            {"t": 34, "shard": 2, "batch": 1, "type": "arrive"},
        ],
    )


def group2_restart_fail():
    # 3 分片 / 2 批:fail 策略。批次1 分片1 失联 -> 超时整批 fail;
    # 分片1 重启先补一条批次1 迟到(滚入批次2),批次2 全员到达 pass。
    return dict(
        shards=3, timeout=10, policy="fail", batches=2,
        events=[
            {"t": 0, "shard": 0, "batch": 1, "type": "arrive"},
            {"t": 1, "shard": 2, "batch": 1, "type": "arrive"},
            {"t": 3, "shard": 1, "batch": 1, "type": "lost"},
            {"t": 10, "shard": 1, "batch": 1, "type": "timeout"},
            {"t": 12, "shard": 1, "batch": 1, "type": "arrive"},   # 迟到 -> 批次2
            {"t": 13, "shard": 0, "batch": 2, "type": "arrive"},
            {"t": 14, "shard": 2, "batch": 2, "type": "arrive"},
            # 批次2 已含滚入的分片1,此处重复其批次1 到达(重试),必须幂等
            {"t": 15, "shard": 1, "batch": 1, "type": "arrive"},
        ],
    )


def group3_out_of_order():
    # 4 分片 / 3 批:事件整体乱序投递;批次1 缺分片3 -> released,
    # 分片3 迟到滚入批次2;批次2 凑齐 pass;批次3 缺人超时 released。
    return dict(
        shards=4, timeout=20, policy="release", batches=3,
        events=[
            {"t": 40, "shard": 2, "batch": 3, "type": "arrive"},
            {"t": 41, "shard": 1, "batch": 3, "type": "arrive"},
            {"t": 22, "shard": 3, "batch": 2, "type": "arrive"},
            {"t": 21, "shard": 3, "batch": 1, "type": "arrive"},  # 迟到 -> 批次2
            {"t": 20, "shard": 2, "batch": 1, "type": "timeout"},
            {"t": 60, "shard": 0, "batch": 3, "type": "timeout"},
            {"t": 1, "shard": 2, "batch": 1, "type": "arrive"},
            {"t": 23, "shard": 0, "batch": 2, "type": "arrive"},
            {"t": 2, "shard": 1, "batch": 1, "type": "arrive"},
            {"t": 24, "shard": 1, "batch": 2, "type": "arrive"},
            {"t": 3, "shard": 0, "batch": 1, "type": "arrive"},
            {"t": 25, "shard": 2, "batch": 2, "type": "arrive"},
        ],
    )


GROUPS = [
    ("G1-lost-release", group1_lost_release, {1: "released", 2: "pass"}),
    ("G2-restart-fail", group2_restart_fail, {1: "fail", 2: "pass"}),
    ("G3-out-of-order", group3_out_of_order, {1: "released", 2: "pass", 3: "released"}),
]


# --------------------------- 投递变体构造 ---------------------------

def variants(base, seed):
    """同一事件集合的三种投递形态:顺序 / 乱序 / 乱序+重复(重试)。"""
    rng = random.Random(seed)
    ordered = list(base["events"])

    shuffled = ordered[:]
    rng.shuffle(shuffled)

    retried = shuffled[:]
    for e in rng.sample(ordered, k=max(1, len(ordered) // 3)):
        retried.append(dict(e))  # 重复消息,模拟重试
    rng.shuffle(retried)

    def wrap(evs):
        req = dict(base)
        req["events"] = evs
        return req

    return [("in_order", wrap(ordered)),
            ("shuffled", wrap(shuffled)),
            ("retry", wrap(retried))]


# --------------------------- 断言工具 ---------------------------

FAILURES = []


def check(cond, msg):
    if not cond:
        FAILURES.append(msg)


def assert_consistent(name, base, expected):
    digest_set, results_set, decision_set = set(), set(), set()
    for vname, req in variants(base, seed=hash(name) & 0xffff):
        r = simulate(req)
        results = {b["id"]: b["result"] for b in r["batches"]}

        check(results == expected,
              "%s/%s 批次结果 %s != 期望 %s" % (name, vname, results, expected))
        # 无永久挂死:凡有 timeout 的批次都必须收敛,不允许 pending
        timed_batches = {e["batch"] for e in base["events"] if e["type"] == "timeout"}
        check(all(results[b] != "pending" for b in timed_batches),
              "%s/%s 超时批次仍 pending(挂死): %s" % (name, vname, results))
        # 同批所有分片结论一致,且等于批次级结果
        for b, verdict in results.items():
            same = {d["verdict"] for d in r["decisions"] if d["batch"] == b}
            check(same == {verdict},
                  "%s/%s 批次%s 分片结论分裂: %s" % (name, vname, b, same))
        # 决策集合规范化后跨变体一致
        dec = sorted((d["shard"], d["batch"], d["verdict"]) for d in r["decisions"])
        digest_set.add(r["digest"])
        results_set.add(tuple(sorted(results.items())))
        decision_set.add(tuple(dec))

    check(len(digest_set) == 1,
          "%s 三种投递变体 digest 不一致: %s" % (name, digest_set))
    check(len(results_set) == 1, "%s 批次结果跨变体不一致" % name)
    check(len(decision_set) == 1, "%s 逐分片决策跨变体不一致" % name)
    return digest_set.pop(), results_set.pop()


def test_cli_roundtrip():
    """CLI 子进程入口输出与库调用一致,且输出 JSON 含规定字段。"""
    req = group1_lost_release()
    td = tempfile.mkdtemp(prefix="barrier-cli-", dir=os.environ.get("TEST_TMP"))
    try:
        ip = os.path.join(td, "in.json")
        op = os.path.join(td, "out.json")
        with open(ip, "w", encoding="utf-8") as f:
            json.dump(req, f)
        proc = subprocess.run([sys.executable, os.path.join(ROOT, "barrier.py"), ip, op],
                              capture_output=True, text=True)
        check(proc.returncode == 0, "CLI 退出码非0: %s" % proc.stderr)
        with open(op, encoding="utf-8") as f:
            out = json.load(f)
    finally:
        for fn in os.listdir(td):
            os.remove(os.path.join(td, fn))
        os.rmdir(td)
    check(set(out.keys()) >= {"batches", "decisions", "digest", "trace"},
          "CLI 输出缺少规定字段: %s" % list(out.keys()))
    check(out["digest"] == simulate(req)["digest"], "CLI 与库调用 digest 不一致")


def test_healthy_and_pending():
    # 健康基线:全员准时到达 -> 全 pass
    healthy = dict(shards=3, timeout=10, policy="release", batches=1, events=[
        {"t": i, "shard": i, "batch": 1, "type": "arrive"} for i in range(3)])
    r = simulate(healthy)
    check(r["batches"][0]["result"] == "pass", "健康批次未 pass")
    check(len({d["verdict"] for d in r["decisions"]}) == 1, "健康批次决策分裂")

    # 流结束仍开放且未超时 -> pending(如实反映在途,但没有任何分片被永久挂死的路径)
    inflight = dict(shards=3, timeout=10, policy="release", batches=1, events=[
        {"t": 0, "shard": 0, "batch": 1, "type": "arrive"}])
    r2 = simulate(inflight)
    check(r2["batches"][0]["result"] == "pending", "在途批次应为 pending")
    check({d["verdict"] for d in r2["decisions"]} == {"pending"}, "pending 批次决策应一致")


def test_policy_equivalence():
    # 同一故障事件集,仅 policy 不同:release -> released;fail -> fail,各自仍一致
    base = group1_lost_release()
    rr = simulate(base)
    fail_req = dict(base, policy="fail")
    rf = simulate(fail_req)
    check(rr["batches"][0]["result"] == "released", "release 策略未放行")
    check(rf["batches"][0]["result"] == "fail", "fail 策略未整批失败")
    check(all(d["verdict"] == "fail" for d in rf["decisions"] if d["batch"] == 1),
          "fail 批次存在非 fail 分片结论")


def test_property_fuzz(rounds=50):
    rng = random.Random(20261116)
    for i in range(rounds):
        shards = rng.randint(2, 5)
        batches = rng.randint(1, 3)
        policy = rng.choice(["release", "fail"])
        events = []
        for b in range(1, batches + 1):
            t0 = b * 100
            for s in range(shards):
                if rng.random() < 0.75:
                    events.append({"t": t0 + rng.randint(0, 25), "shard": s,
                                   "batch": b, "type": "arrive"})
                elif rng.random() < 0.5:
                    events.append({"t": t0 + rng.randint(0, 10), "shard": s,
                                   "batch": b, "type": "lost"})
            if rng.random() < 0.8:
                events.append({"t": t0 + 30, "shard": 0, "batch": b, "type": "timeout"})
        base = dict(shards=shards, timeout=30, policy=policy,
                    batches=batches, events=events)
        digests = set()
        for vname, req in variants(base, seed=1000 + i):
            r = simulate(req)
            # 不变量1:同批分片结论恒等
            for br in r["batches"]:
                same = {d["verdict"] for d in r["decisions"] if d["batch"] == br["id"]}
                check(same == {br["result"]},
                      "fuzz#%d/%s 批次%s 分裂" % (i, vname, br["id"]))
            # 不变量2:超时批次绝不 pending
            timed = {e["batch"] for e in events if e["type"] == "timeout"}
            check(all(br["result"] != "pending" for br in r["batches"]
                      if br["id"] in timed),
                  "fuzz#%d/%s 超时批次挂死" % (i, vname))
            # 不变量3:裁决取值合法
            check(all(br["result"] in {"pass", "fail", "released", "pending"}
                      for br in r["batches"]),
                  "fuzz#%d/%s 非法裁决" % (i, vname))
            digests.add(r["digest"])
        check(len(digests) == 1, "fuzz#%d 跨投递 digest 不一致" % i)


def main():
    print("== 三组故障注入序列复跑(顺序/乱序/重试 三变体) ==")
    summary = []
    for name, builder, expected in GROUPS:
        digest, results = assert_consistent(name, builder(), expected)
        summary.append((name, dict(results), digest))
        print("  %-16s results=%s digest=%s" % (name, dict(results), digest[:12]))

    print("== 附加测试 ==")
    test_cli_roundtrip()
    test_healthy_and_pending()
    test_policy_equivalence()
    test_property_fuzz()

    if FAILURES:
        print("\nFAIL %d 项:" % len(FAILURES))
        for m in FAILURES:
            print("  - " + m)
        return 1
    print("\nALL CHECKS PASSED")
    print("决策摘要(digest)跨乱序/重试完全一致,无分片挂死、无批次结论分裂。")
    return 0


if __name__ == "__main__":
    sys.exit(main())