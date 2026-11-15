"""验收驱动。

用法：python3 tests/acceptance.py

1. 三源四租户固定拥塞轨迹：与 tests/reference.py 的朴素独立模拟器逐 tick
   对比 dispatch、两级余额、waits、segments、service 与 digest；检查余额
   不为负、赤字公平、单源暂停时其余源持续前进。
2. before-debit / after-commit / after-restore 三类强制退出 + 续跑；
   对至少 10% 带唯一 seq 的事件做重复并打乱输入；结果与不中断执行逐项
   相同，重送的已提交页组不产生额外扣账。
"""

import copy
import json
import random
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tests"))

from reference import run as reference_run  # noqa: E402

SCHED = ROOT / "sched.py"
WORK = ROOT / "artifacts" / "acceptance"

TENANTS = ["alpha", "bravo", "charlie", "delta"]
WEIGHTS = {"alpha": 1, "bravo": 2, "charlie": 3, "delta": 4}
# 公平窗口专用租户：全程只有 s1 队列、持续非空、赤字从不重置
FAIR_TENANTS = ["fa", "fb", "fc", "fd"]
FAIR_WEIGHTS = {"fa": 1, "fb": 2, "fc": 3, "fd": 4}
SOURCES = ["s1", "s2", "s3"]


def build_trace():
    events = []
    seq = 1

    def add(event):
        nonlocal seq
        event = dict(event)
        event["seq"] = seq
        seq += 1
        events.append(event)

    # ---- Phase A（tick 0..5）：三源拥塞 + s3 暂停 + 预算变更 ----
    # 每 tick 每租户在 s2/s3 各到两个 cost=1 页组，注入量超过小预算，
    # 必然存在“两级预算合格但本 tick 轮转没轮到”的 rotation 等待。
    for tick in range(6):
        for tenant in TENANTS:
            for sub in range(2):
                add(
                    {
                        "kind": "arrival",
                        "tick": tick,
                        "source": "s2",
                        "tenant": tenant,
                        "task": "job-a",
                        "part": tick * 2 + sub,
                        "cost": 1,
                    }
                )
                add(
                    {
                        "kind": "arrival",
                        "tick": tick,
                        "source": "s3",
                        "tenant": tenant,
                        "task": "job-b",
                        "part": tick * 2 + sub,
                        "cost": 1,
                    }
                )

    # s3 在 tick 2..4 暂停（暂停只影响 s3）
    add({"kind": "pause", "tick": 2, "source": "s3", "paused": True})
    add({"kind": "pause", "tick": 5, "source": "s3", "paused": False})

    # 超重页组：tick 0 到达，成本 5 高于初期两级预算，tick 5 预算放松后
    # 才能派发。等待期间它从未“合格”，等待原因为 budget 而非 rotation。
    # 独立租户 hefty（权重 5）保证赤字攒够后能被服务。
    add(
        {
            "kind": "arrival",
            "tick": 0,
            "source": "s2",
            "tenant": "hefty",
            "task": "heavy",
            "part": 0,
            "cost": 5,
        }
    )

    # 预算变更：tick 3 收紧 s2（仍可派发一个 cost=1 页组），tick 4 调
    # 全局预算，tick 5 放松
    add({"kind": "source_budget", "tick": 3, "source": "s2", "budget": 1})
    add({"kind": "source_budget", "tick": 5, "source": "s2", "budget": 8})
    add({"kind": "global_budget", "tick": 1, "budget": 10})
    add({"kind": "global_budget", "tick": 2, "budget": 20})
    add({"kind": "global_budget", "tick": 4, "budget": 6})
    add({"kind": "global_budget", "tick": 5, "budget": 40})

    # ---- 清理 tick：tick 5 用大预算清空 Phase A 积压 ----
    add({"kind": "source_budget", "tick": 5, "source": "s3", "budget": 40})

    # ---- Phase B 说明：赤字公平由独立子模拟 build_fair_trace 验证 ----
    # 拥塞轨迹保持三源四租户，避免租户集合干扰纯权重比例的观察。

    sim = {
        "horizon": 16,
        "global_budget": 4,
        "source_budget": {"s1": 4, "s2": 4, "s3": 4},
        "weights": {**WEIGHTS, "hefty": 5},
        "events": events,
    }
    return sim


def build_fair_trace():
    """四租户单源、预算恰好等于每轮权重和：连续积压服务量严格按权重。

    每租户 50 个 cost=1 页组（窗口内队列始终非空），每 tick 全局预算 10
    （=1+2+3+4），一轮派发完预算即尽，10 tick 各租户恰得 10/20/30/40。
    """
    events = []
    seq = 1
    for tenant in FAIR_TENANTS:
        for part in range(50):
            events.append(
                {
                    "kind": "arrival",
                    "tick": 0,
                    "seq": seq,
                    "source": "s1",
                    "tenant": tenant,
                    "task": "fair-" + tenant,
                    "part": part,
                    "cost": 1,
                }
            )
            seq += 1
    return {
        "horizon": 10,
        "global_budget": 10,
        "source_budget": {"s1": 10},
        "weights": dict(FAIR_WEIGHTS),
        "events": events,
    }


def run_cli(sim, name, state=None, fail_at=None, expect=(0, 75)):
    WORK.mkdir(parents=True, exist_ok=True)
    sim_path = WORK / (name + ".sim.json")
    out_path = WORK / (name + ".out.json")
    sim_path.write_text(json.dumps(sim), encoding="utf-8")
    cmd = [sys.executable, "-B", str(SCHED), str(sim_path), str(out_path)]
    if state is not None:
        cmd += ["--state", str(state)]
    if fail_at is not None:
        cmd += ["--fail-at", fail_at]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode not in expect:
        raise AssertionError(
            "unexpected exit %s for %s\nstderr=%s"
            % (proc.returncode, name, proc.stderr)
        )
    if proc.returncode == 0:
        return json.loads(out_path.read_text(encoding="utf-8"))
    return None


def check(label, condition, detail=""):
    if not condition:
        raise AssertionError("FAILED: " + label + (" " + detail if detail else ""))
    print("  ok -", label)


def without_digest(report):
    return {k: v for k, v in report.items() if k != "digest"}


def main():
    WORK.mkdir(parents=True, exist_ok=True)
    sim = build_trace()

    print("== 验收 1：固定拥塞轨迹 vs 朴素独立模拟器 ==")
    expected = reference_run(copy.deepcopy(sim))
    actual = run_cli(sim, "baseline")

    check(
        "dispatch 与参考逐条相同",
        actual["dispatch"] == expected["dispatch"],
    )
    check(
        "balances 逐 tick（含两级余额）与参考相同",
        actual["balances"] == expected["balances"],
    )
    check("service 与参考相同", actual["service"] == expected["service"])
    check(
        "paused_ticks 与参考相同",
        actual["paused_ticks"] == expected["paused_ticks"],
    )
    check(
        "backlog_segments 与参考相同",
        actual["backlog_segments"] == expected["backlog_segments"],
    )
    check("waits 与参考相同", actual["waits"] == expected["waits"])
    check("digest 与参考一致", actual["digest"] == expected["digest"])

    # 余额永不为负
    for row in actual["balances"]:
        check("全局余额非负 tick=%d" % row["tick"], row["global"] >= 0)
        for src, value in row["source"].items():
            check(
                "源余额非负 tick=%d src=%s" % (row["tick"], src),
                value >= 0,
            )

    # 等待原因三类都出现
    reasons = {w["reason"] for w in actual["waits"]}
    check(
        "等待原因覆盖 budget/paused/rotation 三类",
        {"budget", "paused", "rotation"}.issubset(reasons),
        str(reasons),
    )

    # 暂停隔离：tick 2..4 s3 零派发，s2 持续前进
    for tick in (2, 3, 4):
        s3_dispatched = [
            d for d in actual["dispatch"]
            if d["tick"] == tick and d["source"] == "s3"
        ]
        check("tick %d 暂停源 s3 无派发" % tick, not s3_dispatched)
        other = [
            d for d in actual["dispatch"]
            if d["tick"] == tick and d["source"] != "s3"
        ]
        check("tick %d 其余源仍在前进" % tick, len(other) > 0)
    check(
        "paused_ticks 记录 s3 暂停 3 tick",
        actual["paused_ticks"]["s3"] == 3,
    )

    # Phase B 赤字公平：独立四租户子模拟，预算充裕、全程连续积压同时合格
    fair_sim = build_fair_trace()
    fair_actual = run_cli(fair_sim, "fair")
    fair_expected = reference_run(copy.deepcopy(fair_sim))
    fair = dict(fair_actual["service"])
    ratio_ok = all(
        fair[t] == FAIR_WEIGHTS[t] * 10 for t in FAIR_TENANTS
    )
    check(
        "连续积压且同时合格的租户按整数赤字权重 1:2:3:4（各 10/20/30/40）",
        ratio_ok,
        str(fair),
    )
    check(
        "公平子模拟与参考模拟器逐项一致",
        without_digest(fair_actual) == without_digest(fair_expected),
    )
    check(
        "公平子模拟 digest 一致",
        fair_actual["digest"] == fair_expected["digest"],
    )
    for row in fair_actual["balances"]:
        check(
            "公平子模拟余额非负 tick=%d" % row["tick"],
            row["global"] >= 0 and all(v >= 0 for v in row["source"].values()),
        )

    print("== 验收 2：强制退出、原子恢复、乱序与重复事件 ==")

    # 2a. 三类失败注入 + 续跑，结果与不中断执行逐项相同
    for fail_at in ("before-debit:1", "after-commit:1", "after-restore"):
        tag = fail_at.replace(":", "-")
        state = WORK / ("state." + tag + ".json")
        if state.exists():
            state.unlink()
        # 第一次：after-restore 需要先有检查点，因此先跑一次 after-commit
        if fail_at == "after-restore":
            run_cli(sim, tag + ".seed", state=state, fail_at="after-commit:1")
        result = run_cli(sim, tag + ".crash", state=state, fail_at=fail_at)
        check(fail_at + " 触发退出码 75", result is None)
        resumed = run_cli(sim, tag + ".resume", state=state)
        check(
            fail_at + " 续跑输出与不中断执行逐项相同",
            without_digest(resumed) == without_digest(actual),
        )
        check(
            fail_at + " 续跑 digest 一致",
            resumed["digest"] == actual["digest"],
        )

    # 2b. 中段崩溃（第 3 次派发前后），覆盖多提交后的游标/赤字还原
    for fail_at in ("before-debit:3", "after-commit:3"):
        tag = fail_at.replace(":", "-") + ".mid"
        state = WORK / ("state." + tag + ".json")
        if state.exists():
            state.unlink()
        run_cli(sim, tag + ".crash", state=state, fail_at=fail_at)
        resumed = run_cli(sim, tag + ".resume", state=state)
        check(
            fail_at + " 续跑与不中断执行逐项相同",
            without_digest(resumed) == without_digest(actual),
        )

    # 2c. 打乱 + 重复至少 10% 的带唯一 seq 事件
    unique_events = sim["events"]
    rng = random.Random(20261115)
    duplicated = rng.sample(unique_events, k=max(1, len(unique_events) // 8))
    check(
        "重复事件占比 >= 10%",
        len(duplicated) >= 0.10 * len(unique_events),
        "%d/%d" % (len(duplicated), len(unique_events)),
    )
    shuffled = copy.deepcopy(unique_events) + copy.deepcopy(duplicated)
    for idx, event in enumerate(shuffled):
        event["_idx"] = idx
    rng.shuffle(shuffled)
    sim_shuffled = copy.deepcopy(sim)
    sim_shuffled["events"] = shuffled

    shuffled_report = run_cli(sim_shuffled, "shuffled")
    check(
        "乱序+重复输入的输出与规范输入相同",
        without_digest(shuffled_report) == without_digest(actual),
    )
    check(
        "乱序+重复输入 digest 相同（输入顺序与重复次数不影响摘要）",
        shuffled_report["digest"] == actual["digest"],
    )

    # 2d. 重送一个已提交页组（在更晚 tick 再投递同 (source,task,part)）
    first_dispatch = actual["dispatch"][0]
    sim_replay = copy.deepcopy(sim)
    sim_replay["events"] = sim_replay["events"] + [
        {
            "kind": "arrival",
            "tick": 10,
            "seq": 10_000_001,
            "source": first_dispatch["source"],
            "tenant": first_dispatch["tenant"],
            "task": first_dispatch["task"],
            "part": first_dispatch["part"],
            "cost": first_dispatch["cost"],
        }
    ]
    replay_report = run_cli(sim_replay, "redeliver")
    check(
        "重送已提交页组不新增派发记录",
        replay_report["dispatch"] == actual["dispatch"],
    )
    check(
        "重送已提交页组不改变两级余额",
        replay_report["balances"] == actual["balances"],
    )
    check(
        "重送已提交页组不改变 service 与 digest",
        replay_report["service"] == actual["service"]
        and replay_report["digest"] == actual["digest"],
    )

    # 2e. 乱序输入叠加崩溃恢复，仍与不中断规范执行一致
    state = WORK / "state.shuffled-crash.json"
    if state.exists():
        state.unlink()
    run_cli(
        sim_shuffled, "shuffled.crash", state=state, fail_at="after-commit:2"
    )
    resumed = run_cli(sim_shuffled, "shuffled.resume", state=state)
    check(
        "乱序输入崩溃续跑与不中断执行逐项相同",
        without_digest(resumed) == without_digest(actual),
    )

    print("\n全部验收通过。")


if __name__ == "__main__":
    main()
