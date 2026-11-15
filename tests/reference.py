"""独立朴素参考模拟器。

与 sched.py 完全独立实现（不导入被测模块），只用最直白的过程式写法
重放同一份规格：两级整数预算、按租户 ID 的赤字轮转、(source, task,
part) 去重、等待原因分类与连续积压段。供验收脚本逐条比对。
"""

import hashlib
import json


def _sort_key_tenant(value):
    return (isinstance(value, (int, float)), value, str(value))


def run(sim):
    weights = {str(k): int(v) for k, v in sim["weights"].items()}
    src_budget = {str(k): int(v) for k, v in sim["source_budget"].items()}
    glob_budget = int(sim["global_budget"])
    horizon = int(sim["horizon"])

    events = sorted(
        sim["events"],
        key=lambda e: (e["tick"], e["seq"], e.get("_idx", 0)),
    )

    tenants = sorted(weights, key=_sort_key_tenant)
    sources = sorted(src_budget)

    glob_left = glob_budget
    src_left = dict(src_budget)
    paused = {s: False for s in sources}
    paused_ticks = {s: 0 for s in sources}
    deficit = {t: 0 for t in tenants}
    cursor = tenants[0] if tenants else None

    pending = []                       # 列表，元素为普通 dict
    seen = set()
    committed = set()
    dispatch = []
    balances = []
    service = {t: 0 for t in tenants}
    segments = {t: [] for t in tenants}
    open_seg = {t: None for t in tenants}
    waits = []
    event_index = 0

    def gid_of(group):
        return group["source"], group["task"], group["part"]

    def gkey(group):
        return group["arrival_tick"], group["task"], group["part"]

    def close_segment(tenant, end_tick):
        seg = open_seg[tenant]
        if seg is not None:
            seg["end"] = end_tick
            segments[tenant].append(seg)
            open_seg[tenant] = None

    for tick in range(horizon):
        # ---- 事件应用 ----
        while event_index < len(events) and events[event_index]["tick"] == tick:
            ev = events[event_index]
            kind = ev["kind"]
            if kind == "arrival":
                src = str(ev["source"])
                tenant = str(ev["tenant"])
                gid = (src, ev["task"], ev["part"])
                if gid not in seen:
                    seen.add(gid)
                    if gid not in committed:
                        if tenant not in deficit:
                            tenants.append(tenant)
                            tenants.sort(key=_sort_key_tenant)
                            deficit[tenant] = 0
                            service[tenant] = 0
                            segments[tenant] = []
                            open_seg[tenant] = None
                            weights[tenant] = int(
                                ev.get("weight", weights.get(tenant, 1))
                            )
                        if src not in src_left:
                            sources.append(src)
                            sources.sort()
                            src_left[src] = src_budget.get(src, ev.get("budget", 0))
                            paused[src] = False
                            paused_ticks[src] = 0
                        pending.append(
                            {
                                "source": src,
                                "tenant": tenant,
                                "task": ev["task"],
                                "part": ev["part"],
                                "cost": int(ev["cost"]),
                                "arrival_tick": int(ev["tick"]),
                                "ready_seen": False,
                            }
                        )
            elif kind == "pause":
                paused[str(ev["source"])] = bool(ev["paused"])
            elif kind == "source_budget":
                src_budget[str(ev["source"])] = int(ev["budget"])
            elif kind == "global_budget":
                glob_budget = int(ev["budget"])
            event_index += 1

        for src in sources:
            paused_ticks[src] += 1 if paused[src] else 0

        # ---- 当 tick 两级预算重装，余额不结转 ----
        glob_left = glob_budget
        src_left = {s: src_budget[s] for s in sources}
        for group in pending:
            group["ready_seen"] = False

        # ---- 赤字轮转：每租户每轮只访问/加一次权，整轮零派发结束 tick ----
        n_tenants = len(tenants)
        if n_tenants and cursor not in tenants:
            cursor = tenants[0]
        visited = set()
        served = 0
        tick_done = False

        def any_budget_eligible():
            for g in pending:
                if not paused[g["source"]] and g["cost"] <= min(
                    glob_left, src_left[g["source"]]
                ):
                    return True
            return False

        while n_tenants and not tick_done:
            tenant = cursor
            if tenant in visited:
                if served == 0:
                    break
                visited = set()
                served = 0
                continue
            visited.add(tenant)
            queue = [g for g in pending if g["tenant"] == tenant]
            # 每次访问都刷新全局合格标记（与调度器同语义）
            for group in pending:
                if (
                    not paused[group["source"]]
                    and group["cost"] <= min(
                        glob_left, src_left[group["source"]]
                    )
                ):
                    group["ready_seen"] = True
            if queue:
                deficit[tenant] += weights[tenant]
                # 一次访问内持续派发，直到没有合格页组
                while True:
                    eligible = []
                    for group in pending:
                        if group["tenant"] != tenant:
                            continue
                        if (
                            deficit[tenant] >= group["cost"]
                            and not paused[group["source"]]
                            and group["cost"]
                            <= min(glob_left, src_left[group["source"]])
                        ):
                            eligible.append(group)
                    if not eligible:
                        break
                    eligible.sort(key=gkey)
                    group = eligible[0]
                    cost = group["cost"]
                    glob_left -= cost
                    src_left[group["source"]] -= cost
                    deficit[tenant] -= cost
                    service[tenant] += cost
                    gid = gid_of(group)
                    committed.add(gid)
                    pending = [g for g in pending if gid_of(g) != gid]
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
                    served += 1
                    rest = [g for g in pending if g["tenant"] == tenant]
                    if not rest:
                        deficit[tenant] = 0
                        close_segment(tenant, tick)
                        break
                    for group2 in pending:
                        if (
                            not paused[group2["source"]]
                            and group2["cost"]
                            <= min(glob_left, src_left[group2["source"]])
                        ):
                            group2["ready_seen"] = True
            else:
                deficit[tenant] = 0

            cursor = tenants[(tenants.index(tenant) + 1) % n_tenants]
            if not any_budget_eligible():
                tick_done = True
                continue
            if len(visited) == n_tenants:
                if served == 0:
                    break
                visited = set()
                served = 0

        # ---- tick 收尾 ----
        for group in sorted(pending, key=lambda g: (gkey(g), g["source"])):
            if paused[group["source"]]:
                reason = "paused"
            elif group["ready_seen"]:
                reason = "rotation"
            else:
                reason = "budget"
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
                "global": glob_left,
                "source": {s: src_left[s] for s in sources},
            }
        )

    for tenant in tenants:
        close_segment(tenant, horizon - 1)

    report = {
        "dispatch": dispatch,
        "balances": balances,
        "service": {t: service[t] for t in tenants},
        "paused_ticks": {s: paused_ticks[s] for s in sources},
        "backlog_segments": {
            t: sorted(segments[t], key=lambda s: (s["start"], s["end"]))
            for t in tenants
        },
        "waits": waits,
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    report["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report
