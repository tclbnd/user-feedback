"""Build deterministic replay scenarios for the acceptance tests."""

import json
import random
from pathlib import Path

HERE = Path(__file__).parent
SCEN_DIR = HERE / "scenarios"


def congested():
    """Fixed congested trajectory: 3 sources, 4 tenants, persistent backlog,
    one source pause window, mid-run budget changes and re-delivered groups."""
    events = []
    seq = 0

    def add(ev):
        nonlocal seq
        seq += 1
        ev = dict(ev)
        ev["seq"] = seq
        events.append(ev)

    weights = {"t1": 1, "t2": 2, "t3": 1, "t4": 3}
    tenants = list(weights)
    sources = ["s1", "s2", "s3"]

    # Persistent backlog for every tenant: 3 tasks x 2 parts, spread across
    # the three sources, arriving in ticks 0..2. Costs are 1 or 2. Task names
    # are tenant-qualified so distinct page groups never collide on the
    # (source, task, part) de-dup identity; only the deliberate re-send below
    # reuses an identity.
    rng = random.Random(42)
    arrivals = []
    for tenant in tenants:
        for task in ("load", "scan", "sync"):
            for part in range(2):
                source = sources[(tenants.index(tenant) + task.__len__()
                                  + part) % 3]
                arrivals.append((rng.randint(0, 2), source, tenant,
                                 task + "-" + tenant, part, rng.randint(1, 2)))
    arrivals.sort(key=lambda a: (a[0], a[2], a[1], a[3]))
    for tick, source, tenant, task, part, cost in arrivals:
        add({"kind": "arrival", "tick": tick, "source": source,
             "tenant": tenant, "task": task, "part": part, "cost": cost})

    # Re-delivery of an already-seen page group under a fresh seq (different
    # seq, same (source, task, part)): must be de-duplicated, zero extra debit
    # and the duplicate's inflated cost must be ignored. The group is still
    # pending at tick 4, so a second debit would show up in the balances if the
    # watermark / key de-dup were broken.
    first = arrivals[0]
    add({"kind": "arrival", "tick": 4, "source": first[1],
         "tenant": first[2], "task": first[3], "part": first[4],
         "cost": first[5] + 5})

    # Pause s2 for ticks 3,4,5; resume at tick 6.
    add({"kind": "pause", "tick": 3, "source": "s2", "paused": True})
    add({"kind": "pause", "tick": 6, "source": "s2", "paused": False})

    # Budget changes effective from their tick onward.
    add({"kind": "global_budget", "tick": 8, "budget": 9})
    add({"kind": "source_budget", "tick": 10, "source": "s1", "budget": 6})
    add({"kind": "source_budget", "tick": 12, "source": "s3", "budget": 1})

    # Extra late arrivals so backlog segments re-open after drains.
    for k in range(6):
        tenant = tenants[k % 4]
        add({"kind": "arrival", "tick": 9 + (k % 4),
             "source": sources[k % 3], "tenant": tenant,
             "task": "bulk-%d" % k, "part": 100 + k, "cost": 1 + (k % 2)})

    sim = {
        "horizon": 16,
        "global_budget": 6,
        "source_budget": {"s1": 4, "s2": 3, "s3": 3},
        "weights": weights,
        "events": events,
    }
    return sim


def edge_cases():
    """Seeded smaller scenarios exercising zeros, full pauses, empty ticks,
    late arrivals, identical re-sends and budgets changing per tick."""
    scenarios = []
    for seed in range(6):
        rng = random.Random(1000 + seed)
        sources = ["a", "b"]
        tenants = ["x", "y", "z"]
        weights = {t: rng.randint(1, 4) for t in tenants}
        events = []
        seq = 0

        def add(ev):
            nonlocal seq
            seq += 1
            ev = dict(ev)
            ev["seq"] = seq
            events.append(ev)

        horizon = rng.randint(8, 14)
        n_arrivals = rng.randint(10, 22)
        for serial in range(n_arrivals):
            tick = rng.randint(0, horizon - 1)
            # Unique task serial guarantees distinct (source, task, part)
            # identities; only the explicit re-send below reuses one.
            add({"kind": "arrival", "tick": tick,
                 "source": rng.choice(sources), "tenant": rng.choice(tenants),
                 "task": "%s-%d" % (rng.choice(["job", "etl", "map"]), serial),
                 "part": rng.randint(0, 3), "cost": rng.randint(1, 3)})
        # Exact duplicate re-send (same everything incl. seq semantics: the
        # builder copies it later; here add a same-key arrival, fresh seq).
        if events:
            src = next(e for e in events if e["kind"] == "arrival")
            add({"kind": "arrival", "tick": rng.randint(0, horizon - 1),
                 "source": src["source"], "tenant": src["tenant"],
                 "task": src["task"], "part": src["part"],
                 "cost": src["cost"] + 9})
        if seed % 2 == 0:
            add({"kind": "pause", "tick": rng.randint(1, 3),
                 "source": "a", "paused": True})
            add({"kind": "pause", "tick": rng.randint(5, horizon - 1),
                 "source": "a", "paused": False})
        add({"kind": "global_budget", "tick": rng.randint(2, 4),
             "budget": rng.randint(0, 5)})
        add({"kind": "source_budget", "tick": rng.randint(2, 5),
             "source": "b", "budget": rng.randint(0, 4)})
        sim = {
            "horizon": horizon,
            "global_budget": rng.randint(2, 7),
            "source_budget": {"a": rng.randint(1, 5),
                              "b": rng.randint(1, 5)},
            "weights": weights,
            "events": events,
        }
        scenarios.append(("edge%d" % seed, sim))
    return scenarios


def write_all():
    SCEN_DIR.mkdir(exist_ok=True)
    (SCEN_DIR / "congested.json").write_text(
        json.dumps(congested(), ensure_ascii=False, indent=2),
        encoding="utf-8")
    for name, sim in edge_cases():
        (SCEN_DIR / (name + ".json")).write_text(
            json.dumps(sim, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    write_all()
    print("scenarios written to", SCEN_DIR)
