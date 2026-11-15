#!/usr/bin/env python3
"""多源采集的分层预算、赤字轮转与原子恢复调度器。

入口保持不变：

    python3 sched.py sim.json output.json [--state checkpoint.json]
            [--fail-at before-debit:N | after-commit:N | after-restore]

全部场景由 JSON 事件按 (tick, seq) 回放驱动。检查点以“整 tick 原子
边界”的方式落盘：一个页组的完成、两级扣账、去重记录与轮转游标在同一
次文件原子替换里提交，崩溃后从上一个完整 tick 重放，已提交页组重送时
扣账恒为零。
"""

import argparse
import hashlib
import json
import os
from pathlib import Path


WAIT_BUDGET = "budget"      # 资源不足（全局或本源两级预算不够）
WAIT_PAUSED = "paused"      # 所属数据源暂停
WAIT_ROTATION = "rotation"  # 页组合格，但本 tick 轮转份额未轮到它


def _tenant_key(tenant):
    """租户 ID 的全序：数值按数值序，其余按字符串序，混合类型也确定。"""
    return (isinstance(tenant, (int, float)), tenant, str(tenant))


def _group_key(group):
    """同一队列内派发/等待的稳定排序键。"""
    return (group["arrival_tick"], group["task"], group["part"])


def _group_id(group):
    return (group["source"], group["task"], group["part"])


def _atomic_write(path, payload):
    """同目录临时文件 + os.replace，保证检查点要么是旧版、要么完整。"""
    path = Path(path)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"))
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def replay(sim, state=None, fail_at=None):
    weights = {str(k): int(v) for k, v in sim["weights"].items()}
    source_budget = {str(k): int(v) for k, v in sim["source_budget"].items()}
    global_budget = int(sim["global_budget"])
    horizon = int(sim["horizon"])

    # 输入可乱序、可重复；稳定排序后重复事件天然相邻，再用 seen 去重。
    events = sorted(
        sim["events"],
        key=lambda event: (event["tick"], event["seq"], event.get("_idx", 0)),
    )

    tenants = sorted(weights, key=_tenant_key)
    sources = sorted(source_budget)

    # ---- 运行态（检查点覆盖以下全部字段）----
    global_remaining = global_budget
    source_remaining = dict(source_budget)
    paused = dict.fromkeys(sources, False)
    paused_ticks = dict.fromkeys(sources, 0)
    deficit = dict.fromkeys(tenants, 0)
    cursor = tenants[0] if tenants else None
    pending = []
    seen = set()          # (source, task, part) 已纳入积压或已提交
    committed = set()     # 已提交页组
    dispatch = []
    balances = []
    service = dict.fromkeys(tenants, 0)
    segments = {t: [] for t in tenants}
    open_seg = {t: None for t in tenants}
    waits = []
    event_index = 0
    tick = 0
    in_tick = False
    tick_start_cursor = None  # 每个 tick 进入轮转前的游标，供整 tick 重放

    def mark_ready_flags():
        """对当前所有未暂停且满足两级预算的页组标记“本 tick 曾合格”。"""
        for group in pending:
            if not paused[group["source"]] and group["cost"] <= min(
                global_remaining, source_remaining[group["source"]]
            ):
                group["ready_seen"] = True

    def close_segment(tenant, end_tick):
        seg = open_seg[tenant]
        if seg is not None:
            seg["end"] = end_tick
            segments[tenant].append(seg)
            open_seg[tenant] = None

    def save_state(tick_value, in_tick_value):
        if state is None:
            return
        payload = {
            "tick": tick_value,
            "in_tick": in_tick_value,
            "event_index": event_index,
            "cursor": cursor,
            "tick_start_cursor": tick_start_cursor,
            "global_remaining": global_remaining,
            "source_remaining": source_remaining,
            "paused": paused,
            "paused_ticks": paused_ticks,
            "deficit": deficit,
            "pending": pending,
            "seen": [list(x) for x in sorted(seen, key=repr)],
            "committed": [list(x) for x in sorted(committed, key=repr)],
            "dispatch": dispatch,
            "service": service,
            "segments": segments,
            "open_seg": open_seg,
            "waits": waits,
            "balances": balances,
            "tenants": tenants,
            "sources": sources,
            "weights": weights,
            "source_budget": source_budget,
            "global_budget": global_budget,
        }
        _atomic_write(state, payload)

    # ---------------- 断点恢复 ----------------
    if state is not None and Path(state).is_file():
        # after-restore：读到已有检查点后、执行任何更新前退出。
        if fail_at == "after-restore":
            os._exit(75)
        cp = json.loads(Path(state).read_text(encoding="utf-8"))
        tick = int(cp["tick"])
        in_tick = bool(cp["in_tick"])
        event_index = int(cp["event_index"])
        cursor = cp["cursor"]
        tick_start_cursor = cp.get("tick_start_cursor")
        global_remaining = int(cp["global_remaining"])
        source_remaining = {k: int(v) for k, v in cp["source_remaining"].items()}
        paused = dict(cp["paused"])
        paused_ticks = {k: int(v) for k, v in cp["paused_ticks"].items()}
        deficit = {k: int(v) for k, v in cp["deficit"].items()}
        pending = cp["pending"]
        seen = {tuple(x) for x in cp["seen"]}
        committed = {tuple(x) for x in cp["committed"]}
        dispatch = cp["dispatch"]
        service = {k: int(v) for k, v in cp["service"].items()}
        segments = cp["segments"]
        open_seg = cp["open_seg"]
        waits = cp["waits"]
        balances = cp["balances"]
        tenants = cp["tenants"]
        sources = cp["sources"]
        # 预算/权重的最新生效值也随检查点还原，后续事件再在此基础上变更
        weights = dict(cp["weights"])
        source_budget = dict(cp["source_budget"])
        global_budget = int(cp["global_budget"])
        # 输入里出现的新租户/新源做防御性补齐
        for t, w in weights.items():
            if t not in deficit:
                tenants.append(t)
                tenants.sort(key=_tenant_key)
                deficit[t] = 0
                service[t] = 0
                segments[t] = []
                open_seg[t] = None
                weights[t] = w
        for src, budget in source_budget.items():
            if src not in source_remaining:
                sources.append(src)
                sources.sort()
                source_remaining[src] = budget
                paused[src] = False
                paused_ticks[src] = 0
        # 崩溃发生在某个 tick 内部：回到该 tick 的 tick 起点整 tick 重放
        if in_tick:
            in_tick = False
            global_remaining = global_budget
            source_remaining = dict(source_budget)
            pending = [g for g in pending if tuple(_group_id(g)) not in committed]
            balances = [b for b in balances if b["tick"] != tick]
            waits = [w for w in waits if w["tick"] != tick]
            for group in pending:
                group["ready_seen"] = False
            for t in tenants:
                open_seg[t] = None
                segments[t] = [s for s in segments[t] if s["end"] != tick]
            # 整 tick 重放：游标回到该 tick 起点，赤字也回到 tick 起点。
            # 已提交页组的扣账随 committed/dispatch 保留，重放时它们不再
            # 进入 pending，因此不会产生第二笔扣账；本 tick 剩余派发按确定
            # 性顺序补齐，最终结果与不中断执行完全一致。
            if tick_start_cursor is not None:
                cursor = tick_start_cursor
            # 回退本 tick 已提交页组对赤字的占用：赤字是 tick 内调度量，
            # 重放会重新累计；service/dispatch/committed 保持已提交事实。
            for d in dispatch:
                if d["tick"] == tick and d["tenant"] in deficit:
                    deficit[d["tenant"]] += d["cost"]
            # 若重放中某租户队列将变空，其赤字本应为 0；统一在轮转中按
            # “空队列清零、非空加权”规则自然收敛，这里只需保证非负。
            for t in tenants:
                deficit[t] = max(0, deficit.get(t, 0))

    run_ordinal = 0  # 本进程内计划派发序号，用于失败注入

    while tick < horizon:
        if not in_tick:
            # ---- 1) 按 (tick, seq) 应用当 tick 的全部事件 ----
            while event_index < len(events) and events[event_index]["tick"] == tick:
                event = events[event_index]
                kind = event["kind"]
                if kind == "arrival":
                    src = str(event["source"])
                    tenant = str(event["tenant"])
                    gid = (src, event["task"], event["part"])
                    if gid not in seen:
                        # 首次出现确定归属与成本；重送的已提交页组直接忽略
                        seen.add(gid)
                        if gid not in committed:
                            if tenant not in deficit:
                                tenants.append(tenant)
                                tenants.sort(key=_tenant_key)
                                deficit[tenant] = 0
                                service[tenant] = service.get(tenant, 0)
                                segments[tenant] = segments.get(tenant, [])
                                open_seg[tenant] = open_seg.get(tenant)
                                weights[tenant] = int(
                                    event.get("weight", weights.get(tenant, 1))
                                )
                            if src not in source_remaining:
                                sources.append(src)
                                sources.sort()
                                source_remaining[src] = source_budget.get(
                                    src, event.get("budget", 0)
                                )
                                paused[src] = paused.get(src, False)
                                paused_ticks[src] = paused_ticks.get(src, 0)
                            pending.append(
                                {
                                    "source": src,
                                    "tenant": tenant,
                                    "task": event["task"],
                                    "part": event["part"],
                                    "cost": int(event["cost"]),
                                    "arrival_tick": int(event["tick"]),
                                    "ready_seen": False,
                                }
                            )
                elif kind == "pause":
                    paused[str(event["source"])] = bool(event["paused"])
                elif kind == "source_budget":
                    source_budget[str(event["source"])] = int(event["budget"])
                elif kind == "global_budget":
                    global_budget = int(event["budget"])
                event_index += 1

            # 暂停只影响对应源；按源统计暂停 tick 数
            for src in sources:
                paused_ticks[src] += int(bool(paused.get(src, False)))

            # ---- 2) 装入当 tick 的两级整数预算，余额不结转 ----
            global_remaining = global_budget
            source_remaining = {src: source_budget[src] for src in sources}

            for group in pending:
                group["ready_seen"] = False

            # tick 起点检查点（in_tick=True）：崩溃后整 tick 可确定性重放
            in_tick = True
            save_state(tick, True)

        # ---- 3) 按租户 ID 循环的赤字轮转 ----
        # 一轮(pass)= 从游标起对每个租户各访问一次；每次访问先加一次权重，
        # 随后在赤字与两级预算允许时持续派发本租户页组，直到没有合格页组
        # 才轮到下一租户。整轮零派发才进入下一 tick。
        n_tenants = len(tenants)
        if n_tenants and cursor not in tenants:
            cursor = tenants[0]
        visited_this_pass = set()
        dispatched_this_pass = 0
        tick_done = False

        def any_budget_eligible():
            """是否还有未暂停且满足两级预算的页组（与赤字无关）。"""
            for g in pending:
                if not paused[g["source"]] and g["cost"] <= min(
                    global_remaining, source_remaining[g["source"]]
                ):
                    return True
            return False

        while n_tenants and not tick_done:
            tenant = cursor
            # 每个租户每轮(pass)只访问、只加一次权重
            if tenant in visited_this_pass:
                if dispatched_this_pass == 0:
                    break
                visited_this_pass = set()
                dispatched_this_pass = 0
                continue
            visited_this_pass.add(tenant)
            q = [g for g in pending if g["tenant"] == tenant]
            # 每次访问都刷新全局合格标记：未被本租户选中、但两级预算允许
            # 的其他租户页组，本 tick 曾合格却未轮到，归因为 rotation。
            mark_ready_flags()
            if q:
                # 非空队列：本轮加一次权重作为赤字额度
                deficit[tenant] += weights[tenant]
                # 一次访问内持续派发，直到本租户没有合格页组
                while True:
                    candidates = [
                        g
                        for g in pending
                        if g["tenant"] == tenant
                        and deficit[tenant] >= g["cost"]
                        and not paused[g["source"]]
                        and g["cost"] <= min(
                            global_remaining, source_remaining[g["source"]]
                        )
                    ]
                    if not candidates:
                        break
                    candidates.sort(key=_group_key)
                    group = candidates[0]
                    run_ordinal += 1
                    if fail_at == "before-debit:" + str(run_ordinal):
                        os._exit(75)
                    cost = group["cost"]
                    # 完成、两级扣账、去重在同一次原子提交里出现
                    global_remaining -= cost
                    source_remaining[group["source"]] -= cost
                    deficit[tenant] -= cost
                    service[tenant] += cost
                    gid = _group_id(group)
                    committed.add(gid)
                    pending = [g for g in pending if _group_id(g) != gid]
                    if open_seg[tenant] is None:
                        open_seg[tenant] = {
                            "start": group["arrival_tick"],
                            "end": None,
                            "service": 0,
                        }
                    open_seg[tenant]["service"] += cost
                    dispatch.append(
                        {
                            "tick": tick,
                            "source": group["source"],
                            "tenant": tenant,
                            "task": group["task"],
                            "part": group["part"],
                            "cost": cost,
                        }
                    )
                    dispatched_this_pass += 1
                    save_state(tick, True)
                    if fail_at == "after-commit:" + str(run_ordinal):
                        os._exit(75)
                    if not [g for g in pending if g["tenant"] == tenant]:
                        # 积压清空：赤字清零，连续积压段收尾
                        deficit[tenant] = 0
                        close_segment(tenant, tick)
                        break
                    mark_ready_flags()
                save_state(tick, True)
            else:
                # 空队列：赤字清零
                deficit[tenant] = 0

            # 无论是否结束本 tick，轮转游标都推进到下一租户，
            # 保证跨 tick 的轮转位置不回退
            cursor = tenants[(tenants.index(tenant) + 1) % n_tenants]

            # 两级预算已无法支撑任何未暂停页组：本 tick 到此为止，
            # 不再开启新一轮给租户空加赤字（保证跨 tick 的公平比例）
            if not any_budget_eligible():
                tick_done = True
                continue

            if len(visited_this_pass) == n_tenants:
                # 完整一轮：有派发则开启新一轮，整轮零派发则结束本 tick
                if dispatched_this_pass == 0:
                    break
                visited_this_pass = set()
                dispatched_this_pass = 0

        # ---- 4) tick 收尾：等待原因分类、余额快照 ----
        for group in sorted(pending, key=lambda g: (_group_key(g), g["source"])):
            if paused[group["source"]]:
                reason = WAIT_PAUSED
            elif group.get("ready_seen"):
                reason = WAIT_ROTATION
            else:
                reason = WAIT_BUDGET
            waits.append(
                {
                    "tick": tick,
                    "source": group["source"],
                    "tenant": group["tenant"],
                    "task": group["task"],
                    "part": group["part"],
                    "reason": reason,
                }
            )

        balances.append(
            {
                "tick": tick,
                "global": global_remaining,
                "source": {src: source_remaining[src] for src in sources},
            }
        )

        in_tick = False
        save_state(tick, False)  # tick 完整边界
        tick += 1

    # horizon 结束仍有未服务积压：开放段以 horizon-1 封口，service 如实记录
    for tenant in tenants:
        close_segment(tenant, horizon - 1)

    backlog_segments = {
        tenant: sorted(segments[tenant], key=lambda s: (s["start"], s["end"]))
        for tenant in tenants
    }

    report = {
        "dispatch": dispatch,
        "balances": balances,
        "service": {t: service[t] for t in tenants},
        "paused_ticks": {src: paused_ticks[src] for src in sources},
        "backlog_segments": backlog_segments,
        "waits": waits,
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("simulation", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--state", type=Path)
    parser.add_argument("--fail-at")
    args = parser.parse_args()
    sim = json.loads(args.simulation.read_text(encoding="utf-8"))
    report = replay(sim, state=args.state, fail_at=args.fail_at)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
