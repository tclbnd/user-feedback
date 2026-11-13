"""Multi-region business-day settlement over offline tz tables.

Each apply batch carries tz_version, regions (offset tables) and events.
Local times are resolved to UTC purely from those tables: fall-back folds
need an explicit fold=0/1, spring-forward gaps are rejected, and business
days run local midnight to local midnight whatever their UTC length.
Amounts are exact integer minor units; every committed state is kept as a
replayable version.
"""
import argparse
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

UTC = timezone.utc
AMOUNT_RE = re.compile(r"^-?\d+(?:\.\d{1,2})?$")
CONTENT_FIELDS = ("region", "local", "fold", "offset_min", "amount", "void")


def parse_utc(text):
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def fmt_utc(moment):
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def intervals(region_def):
    """(start, end, offset_min) triples, left-closed; None means unbounded."""
    transitions = region_def.get("transitions", [])
    bounds = [parse_utc(t["at_utc"]) for t in transitions]
    offsets = [region_def["initial_offset_min"]] + [t["offset_min"] for t in transitions]
    return list(zip([None] + bounds, bounds + [None], offsets))


def local_candidates(region_def, local):
    """Every UTC instant mapping to naive `local`, ascending, with its offset."""
    found = []
    for start, end, off in intervals(region_def):
        cand = (local - timedelta(minutes=off)).replace(tzinfo=UTC)
        if (start is None or cand >= start) and (end is None or cand < end):
            found.append((cand, off))
    found.sort(key=lambda item: item[0])
    return found


def day_bounds(region_def, date):
    """UTC bounds of one business day; boundaries are unique by problem spec."""
    first = datetime.strptime(date, "%Y-%m-%d")
    start = local_candidates(region_def, first)[0][0]
    end = local_candidates(region_def, first + timedelta(days=1))[0][0]
    return start, end


def parse_amount(text):
    """Exact two-decimal string -> integer minor units, or None if invalid."""
    if not isinstance(text, str) or not AMOUNT_RE.match(text):
        return None
    neg = text.startswith("-")
    body = text[1:] if neg else text
    whole, _, frac = body.partition(".")
    minor = int(whole) * 100 + int((frac + "00")[:2])
    return -minor if neg else minor


def resolve_event(ev, regions):
    """Validate one non-void event. Returns (resolved, reject_code)."""
    region = ev.get("region")
    rdef = regions.get(region)
    if rdef is None:
        return None, "UNKNOWN_REGION"
    try:
        local = datetime.strptime(ev["local"], "%Y-%m-%dT%H:%M:%S")
    except (KeyError, TypeError, ValueError):
        return None, "BAD_LOCAL"
    cands = local_candidates(rdef, local)
    if not cands:
        return None, "NONEXISTENT_LOCAL"
    if len(cands) > 1:
        fold = ev.get("fold")
        if fold not in (0, 1):
            return None, "AMBIGUOUS_LOCAL"
        utc, off = cands[fold]
    else:
        utc, off = cands[0]
    if ev.get("offset_min") is not None and ev["offset_min"] != off:
        return None, "OFFSET_MISMATCH"
    minor = parse_amount(ev.get("amount"))
    if minor is None:
        return None, "BAD_AMOUNT"
    return {"utc": fmt_utc(utc), "offset_min": off,
            "date": ev["local"][:10], "minor": minor}, None


def content_of(ev):
    """Canonical content of a revision, used to detect conflicting replays."""
    return json.dumps({k: ev.get(k) for k in CONTENT_FIELDS}, sort_keys=True)


def empty_state():
    return {"version": 0, "tz_version": None, "regions": {}, "facts": {},
            "seen": {}, "max_rev": {}, "rejected": [], "days": [],
            "versions": {}}


def apply_batch(state, batch):
    regions = batch.get("regions") or {}
    tz_version = batch.get("tz_version")
    old_regions = state["regions"]
    changed_rule_regions = {r for r in set(regions) | set(old_regions)
                            if regions.get(r) != old_regions.get(r)}

    facts = state["facts"]
    seen = state["seen"]
    max_rev = state["max_rev"]
    rejected = {(r["id"], r["rev"]): r["code"] for r in state["rejected"]}
    touched = set()

    for ev in batch.get("events") or []:
        eid, rev = ev.get("id"), ev.get("rev")
        if eid is None or rev is None:
            continue
        key = json.dumps([eid, rev])
        content = content_of(ev)
        if key in seen:
            if seen[key] != content:
                rejected[(eid, rev)] = "CONFLICTING_REVISION"
            continue
        seen[key] = content
        if rev < max_rev.get(eid, -1):
            continue  # stale revision never overwrites
        max_rev[eid] = rev
        if ev.get("void"):
            if eid in facts:  # retract whatever the fact originally booked
                touched.add(facts[eid]["region"])
                del facts[eid]
            continue
        resolved, code = resolve_event(ev, regions)
        if code is not None:
            rejected[(eid, rev)] = code
            continue
        if eid in facts:  # late correction still books its original day
            touched.add(facts[eid]["region"])
        fact = {"rev": rev, "region": ev["region"], "local": ev["local"],
                "amount": ev["amount"]}
        if "fold" in ev:
            fact["fold"] = ev["fold"]
        fact.update(resolved)
        facts[eid] = fact
        touched.add(ev["region"])

    if changed_rule_regions:
        # tz database switch: re-resolve only facts in regions whose rules
        # actually changed; other regions need no recomputation.
        for eid, fact in list(facts.items()):
            if fact["region"] not in changed_rule_regions:
                continue
            ev_like = {"region": fact["region"], "local": fact["local"],
                       "amount": fact["amount"]}
            if "fold" in fact:
                ev_like["fold"] = fact["fold"]
            resolved, code = resolve_event(ev_like, regions)
            if code is not None:
                rejected[(eid, fact["rev"])] = code
                del facts[eid]
            else:
                fact.update(resolved)
        touched |= changed_rule_regions

    state["regions"] = regions
    state["tz_version"] = tz_version
    state["rejected"] = [{"id": i, "rev": v, "code": c}
                         for (i, v), c in sorted(rejected.items())]

    days = {(d["region"], d["date"]): d for d in state["days"]}
    for key in [k for k in days if k[0] in touched]:
        del days[key]
    agg = {}
    for fact in facts.values():
        key = (fact["region"], fact["date"])
        if key[0] not in touched:
            continue
        count, minor = agg.get(key, (0, 0))
        agg[key] = (count + 1, minor + fact["minor"])
    for (region, date), (count, minor) in agg.items():
        start, end = day_bounds(regions[region], date)
        days[(region, date)] = {
            "region": region, "date": date, "count": count, "minor": minor,
            "utc_start": fmt_utc(start), "utc_end": fmt_utc(end),
            "seconds": int((end - start).total_seconds())}
    day_list = [days[k] for k in sorted(days)]
    state["days"] = day_list

    events_out = [{"id": eid, "rev": f["rev"], "utc": f["utc"],
                   "region": f["region"], "date": f["date"], "minor": f["minor"]}
                  for eid, f in sorted(facts.items())]
    core = {"tz_version": tz_version, "days": day_list,
            "events": events_out, "rejected": state["rejected"]}
    prev = state["versions"].get(str(state["version"]))
    if prev is not None and all(prev[k] == core[k] for k in core):
        return prev  # repeated batch: nothing new, no version committed

    diff = []
    if prev is not None:
        old = {(d["region"], d["date"]): (d["count"], d["minor"])
               for d in prev["days"]}
        new = {(d["region"], d["date"]): (d["count"], d["minor"])
               for d in day_list}
        for region, date in sorted(set(old) | set(new)):
            old_cm = old.get((region, date), (0, 0))
            new_cm = new.get((region, date), (0, 0))
            if old_cm != new_cm:
                diff.append({"region": region, "date": date,
                             "count_delta": new_cm[0] - old_cm[0],
                             "minor_delta": new_cm[1] - old_cm[1]})
    state["version"] += 1
    snapshot = {"version": state["version"],
                "affected_regions": sorted(touched), "diff": diff, **core}
    state["versions"][str(state["version"])] = snapshot
    return snapshot


def main():
    p = argparse.ArgumentParser()
    p.add_argument("state", type=Path)
    sub = p.add_subparsers(dest="op", required=True)
    a = sub.add_parser("apply")
    a.add_argument("input", type=Path)
    q = sub.add_parser("inspect")
    q.add_argument("--version", type=int)
    opt = p.parse_args()
    try:
        if opt.op == "apply":
            state = (json.loads(opt.state.read_text())
                     if opt.state.exists() else empty_state())
            result = apply_batch(state, json.loads(opt.input.read_text()))
            opt.state.write_text(json.dumps(state) + "\n")
        else:
            state = json.loads(opt.state.read_text())
            want = str(opt.version if opt.version is not None else state["version"])
            result = state["versions"][want]
        print(json.dumps(result, sort_keys=True))
    except (OSError, ValueError, KeyError) as err:
        p.exit(2, str(err) + "\n")


if __name__ == "__main__":
    main()
