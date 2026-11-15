#!/usr/bin/env python3
"""Replay page-group arrivals against a shared tick budget."""

import argparse
import hashlib
import json
import os
from pathlib import Path


def replay(sim, state=None, fail_at=None):
    if fail_at == "after-restore" and state is not None and state.is_file():
        os._exit(75)

    source_budget = dict(sim["source_budget"])
    global_budget = sim["global_budget"]
    paused = dict.fromkeys(source_budget, False)
    paused_ticks = dict.fromkeys(source_budget, 0)
    events = sorted(sim["events"], key=lambda event: (event["tick"], event["seq"]))
    pending = []
    dispatch = []
    balances = []
    service = dict.fromkeys(sim["weights"], 0)
    event_index = 0
    for tick in range(sim["horizon"]):
        while event_index < len(events) and events[event_index]["tick"] == tick:
            event = events[event_index]
            kind = event["kind"]
            if kind == "arrival":
                pending.append(event)
            elif kind == "pause":
                paused[event["source"]] = event["paused"]
            elif kind == "source_budget":
                source_budget[event["source"]] = event["budget"]
            elif kind == "global_budget":
                global_budget = event["budget"]
            event_index += 1

        remaining = global_budget
        per_source = source_budget.copy()
        for source, frozen in paused.items():
            paused_ticks[source] += int(frozen)

        if pending:
            group = pending[0]
            source, cost = group["source"], group["cost"]
            if not paused[source] and cost <= min(remaining, per_source[source]):
                ordinal = len(dispatch) + 1
                if fail_at == f"before-debit:{ordinal}":
                    os._exit(75)
                remaining -= cost
                per_source[source] -= cost
                dispatch.append({"tick": tick, **{field: group[field] for field in (
                    "source", "tenant", "task", "part", "cost",
                )}})
                service[group["tenant"]] += cost
                pending.pop(0)
                if state is not None:
                    state.write_text(json.dumps(dispatch), encoding="utf-8")
                if fail_at == f"after-commit:{ordinal}":
                    os._exit(75)

        balances.append({"tick": tick, "global": remaining, "source": per_source})

    report = {
        "dispatch": dispatch,
        "balances": balances,
        "service": service,
        "paused_ticks": paused_ticks,
        "backlog_segments": {},
        "waits": [],
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
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
