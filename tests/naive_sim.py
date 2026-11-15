"""Independent, deliberately plain reference simulator.

This is written in a different style from ``sched.py`` (plain functions,
deques, local variables, no checkpoint machinery) and re-derives every rule
straight from the specification. Acceptance tests use it as the integer
deficit / hierarchical-budget reference to cross-check the real scheduler.
"""

import hashlib
import json
from collections import deque


def run(sim):
    horizon = sim["horizon"]
    weights = sim["weights"]
    tenants = sorted(weights)
    events = sorted(sim["events"], key=lambda e: (e["tick"], e["seq"]))

    global_cap = sim["global_budget"]
    source_cap = dict(sim["source_budget"])
    paused = {s: False for s in source_cap}

    # key (source, task, part) -> group dict; first arrival wins.
    groups = {}
    queues = {t: deque() for t in tenants}  # keys in arrival order
    deficit = {t: 0 for t in tenants}
    service = {t: 0 for t in tenants}
    segments = []
    open_seg = {t: None for t in tenants}
    dispatch = []
    balances = []
    waits = []
    paused_ticks = {s: 0 for s in source_cap}
    event_index = 0
    cursor = tenants[0]
    carry = False

    def order_key(key):
        # Mirror the real scheduler: spec order (arrival, task, part) with a
        # deterministic source tie-break for distinct same-named groups.
        g = groups[key]
        return (g["arrival_tick"], g["task"], g["part"], g["source"])

    def segment_open(tenant, tick):
        # Mirror the real scheduler: only reuse a segment that is still live
        # at this tick; a segment closed by an earlier drain starts a new one.
        seg = open_seg[tenant]
        if seg is not None and seg["end"] >= tick:
            return
        if seg is not None:
            open_seg[tenant] = None
        new_seg = {"tenant": tenant, "start": tick, "end": tick, "service": 0}
        open_seg[tenant] = new_seg
        segments.append(new_seg)

    for tick in range(horizon):
        # 1) apply this tick's events in (tick, seq) order.
        while event_index < len(events) and events[event_index]["tick"] == tick:
            ev = events[event_index]
            kind = ev["kind"]
            if kind == "arrival":
                key = (ev["source"], ev["task"], ev["part"])
                if key not in groups:
                    groups[key] = {
                        "source": ev["source"],
                        "tenant": ev["tenant"],
                        "task": ev["task"],
                        "part": ev["part"],
                        "cost": ev["cost"],
                        "arrival_tick": tick,
                    }
                    tenant = ev["tenant"]
                    # keep the queue in (arrival tick, task, part) order.
                    inserted = False
                    for pos, existing in enumerate(queues[tenant]):
                        if order_key(key) < order_key(existing):
                            queues[tenant].insert(pos, key)
                            inserted = True
                            break
                    if not inserted:
                        queues[tenant].append(key)
                    segment_open(tenant, tick)
            elif kind == "pause":
                paused[ev["source"]] = ev["paused"]
            elif kind == "source_budget":
                source_cap[ev["source"]] = ev["budget"]
                paused.setdefault(ev["source"], False)
                paused_ticks.setdefault(ev["source"], 0)
            elif kind == "global_budget":
                global_cap = ev["budget"]
            event_index += 1

        # 2) fresh integer budgets each tick; no balance carry-over.
        global_remaining = global_cap
        source_remaining = dict(source_cap)
        for source in paused:
            paused_ticks[source] += int(bool(paused[source]))

        # 3) deficit rotation: exactly one visit per tenant per tick, each
        # non-empty queue earning its weight a single time. A carried or
        # globally-exhausted visit ends the tick early on the same tenant.
        carry_tenant = None
        for _ in range(len(tenants)):
            tenant = cursor
            # Cursor always advances so a busy tenant cannot pin the rotation.
            idx = tenants.index(tenant)
            cursor = tenants[(idx + 1) % len(tenants)]
            queue = queues[tenant]
            exhausted = False
            if not queue:
                deficit[tenant] = 0
                carry = False
            else:
                if not carry:
                    deficit[tenant] += weights[tenant]
                carry = False  # marker consumed: it only starts this visit
                while queue:
                    chosen = None
                    for key in queue:
                        g = groups[key]
                        if paused.get(g["source"], False):
                            continue
                        if (g["cost"] <= global_remaining
                                and g["cost"] <= source_remaining.get(
                                    g["source"], 0)
                                and g["cost"] <= deficit[tenant]):
                            chosen = key
                            break
                    if chosen is None:
                        # Carry across the tick only when the global budget is
                        # exhausted; other blockers rotate on normally.
                        carry = global_remaining <= 0
                        break
                    g = groups[chosen]
                    cost = g["cost"]
                    global_remaining -= cost
                    source_remaining[g["source"]] -= cost
                    deficit[tenant] -= cost
                    service[tenant] += cost
                    dispatch.append({
                        "tick": tick,
                        "source": g["source"],
                        "tenant": tenant,
                        "task": g["task"],
                        "part": g["part"],
                        "cost": cost,
                    })
                    open_seg[tenant]["service"] += cost
                    open_seg[tenant]["end"] = tick
                    groups.pop(chosen)
                    queue.remove(chosen)
                    if not queue:
                        deficit[tenant] = 0
                        open_seg[tenant]["end"] = tick
                        open_seg[tenant] = None
                    if global_remaining <= 0:
                        # Global budget drained: tick ends immediately. Carry
                        # only while backlog and unspent deficit both remain;
                        # a spent-deficit visit still rotates next tick.
                        carry = bool(queue) and deficit[tenant] > 0
                        exhausted = True
                        break
            # Only an explicit carry resumes the same tenant; exhaustion with
            # a completed visit just stops the tick with an advanced cursor.
            if carry:
                carry_tenant = tenant
            if exhausted or carry:
                break
        if carry_tenant is not None:
            cursor = carry_tenant

        # 4) classify everything still pending at tick end.
        for tenant in tenants:
            for key in queues[tenant]:
                g = groups[key]
                if paused.get(g["source"], False):
                    reason = "paused"
                elif (g["cost"] > global_remaining
                      or g["cost"] > source_remaining.get(g["source"], 0)):
                    reason = "budget"
                else:
                    reason = "rotation"
                waits.append({
                    "tick": tick,
                    "source": g["source"],
                    "tenant": tenant,
                    "task": g["task"],
                    "part": g["part"],
                    "reason": reason,
                })
        balances.append({
            "tick": tick,
            "global": global_remaining,
            "source": dict(source_remaining),
        })

    for seg in segments:
        if open_seg.get(seg["tenant"]) is seg:
            seg["end"] = max(horizon - 1, seg["start"])
            open_seg[seg["tenant"]] = None

    report = {
        "dispatch": dispatch,
        "balances": balances,
        "service": service,
        "paused_ticks": paused_ticks,
        "backlog_segments": segments,
        "waits": waits,
    }
    canonical = json.dumps(report, sort_keys=True,
                           separators=(",", ":"), ensure_ascii=False)
    report["digest"] = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return report
