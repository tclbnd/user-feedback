"""遥测累计计数重放:版本化、可崩溃恢复的区间分类与小时汇总。

规则(见 README):
- 同一设备同一 t 保留 rev 最大的样本;内容相同的重复无副作用;同 rev 不同内容报错。
- 相邻样本逐对生成区间:boot 变化 -> reset;间隔 > max_gap -> gap;
  max_rate*dt >= 2^bits 或模增量 > max_rate*dt -> unknown;
  值下降 -> wrap;其余 -> normal。reset/gap/unknown 的 delta 为 null。
- 已知增量(含零)归入 end//3600 的小时,跨小时区间不分摊。
- 每个已消费样本前缀是一个逻辑版本;变更分批为 remove/upsert,无变化不追加。
- 状态原子发布;--crash-before-publish 在可见提交前以 97 退出。
"""
import argparse
import json
import os
import sys
from pathlib import Path


class ConflictError(Exception):
    """同设备同 t 同 rev 但内容不同。"""


def empty_result():
    return {"version": 0, "intervals": [], "hourly": [], "changes": []}


def empty_state():
    return {"cursor": 0, "series": {}, "versions": {}}


def load_state(db):
    if db.exists():
        return json.loads(db.read_text())
    return empty_state()


def publish(db, state):
    tmp = db.with_name(db.name + ".tmp")
    tmp.write_text(json.dumps(state, sort_keys=True) + "\n")
    os.replace(tmp, db)


def make_interval(device, a, b, cfg):
    dt = b["t"] - a["t"]
    if a["boot"] != b["boot"]:
        kind, delta = "reset", None
    elif dt > cfg["max_gap"]:
        kind, delta = "gap", None
    else:
        mod = 1 << cfg["bits"]
        mdelta = (b["value"] - a["value"]) % mod
        budget = cfg["max_rate"] * dt
        if budget >= mod or mdelta > budget:
            kind, delta = "unknown", None
        elif b["value"] < a["value"]:
            kind, delta = "wrap", mdelta
        else:
            kind, delta = "normal", mdelta
    return {"device": device, "start": a["t"], "end": b["t"],
            "kind": kind, "delta": delta}


def interval_key(iv):
    return (iv["device"], iv["start"], iv["end"])


def all_intervals(series, devices):
    out = []
    for dev, by_t in series.items():
        cfg = devices[dev]
        pts = [dict(s, t=int(t)) for t, s in by_t.items()]
        pts.sort(key=lambda s: s["t"])
        for a, b in zip(pts, pts[1:]):
            out.append(make_interval(dev, a, b, cfg))
    out.sort(key=interval_key)
    return out


def compute_hourly(intervals):
    totals = {}
    for iv in intervals:
        if iv["delta"] is not None:
            key = (iv["device"], iv["end"] // 3600)
            totals[key] = totals.get(key, 0) + iv["delta"]
    return [{"device": d, "hour": h, "delta": v}
            for (d, h), v in sorted(totals.items())]


def consume(state, sample, devices):
    """消费一个样本,推进一个逻辑版本并记录快照与变更批次。"""
    dev = sample["device"]
    if dev not in devices:
        raise ConflictError("unknown device: %r" % dev)
    t = str(sample["t"])
    series = state["series"].setdefault(dev, {})
    existing = series.get(t)
    changed = False
    if existing is not None:
        if existing["rev"] == sample["rev"]:
            if (existing["boot"] != sample["boot"]
                    or existing["value"] != sample["value"]):
                raise ConflictError(
                    "conflicting content for device=%r t=%s rev=%s"
                    % (dev, t, sample["rev"]))
        elif existing["rev"] > sample["rev"]:
            pass  # 迟到的旧 rev,保留最大 rev
        else:
            series[t] = {"boot": sample["boot"], "rev": sample["rev"],
                         "value": sample["value"]}
            changed = True
    else:
        series[t] = {"boot": sample["boot"], "rev": sample["rev"],
                     "value": sample["value"]}
        changed = True

    cursor = state["cursor"]
    prev = state["versions"][str(cursor)] if cursor else \
        {"intervals": [], "hourly": []}
    if changed:
        intervals = all_intervals(state["series"], devices)
        hourly = compute_hourly(intervals)
        removed = [iv for iv in prev["intervals"] if iv not in intervals]
        added = [iv for iv in intervals if iv not in prev["intervals"]]
    else:
        intervals, hourly = prev["intervals"], prev["hourly"]
        removed, added = [], []
    version = cursor + 1
    batch = None
    if removed or added:
        batch = {"version": version, "remove": removed, "upsert": added}
    state["versions"][str(version)] = {
        "intervals": intervals, "hourly": hourly, "batch": batch}
    state["cursor"] = version


def result_for(state, version=None):
    cursor = state["cursor"]
    if version is None or version > cursor:
        version = cursor
    if version < 0:
        version = 0
    if version == 0:
        return empty_result()
    snap = state["versions"][str(version)]
    changes = []
    for v in range(1, version + 1):
        batch = state["versions"][str(v)]["batch"]
        if batch:
            changes.append(batch)
    return {"version": version, "intervals": snap["intervals"],
            "hourly": snap["hourly"], "changes": changes}


def apply(source, db, upto=None, crash=False):
    data = json.loads(source.read_text())
    devices = data["devices"]
    samples = data["samples"]
    state = load_state(db)
    target = len(samples) if upto is None else min(max(upto, 0), len(samples))
    while state["cursor"] < target:
        consume(state, samples[state["cursor"]], devices)
    if crash:
        sys.exit(97)  # 可见提交前无清理退出,旧快照仍可读
    publish(db, state)
    return result_for(state)


def inspect(db, version=None):
    if not db.exists():
        return empty_result()
    return result_for(load_state(db), version)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="action", required=True)
    sync = sub.add_parser("apply")
    sync.add_argument("source", type=Path)
    sync.add_argument("state", type=Path)
    sync.add_argument("--upto", type=int)
    sync.add_argument("--crash-before-publish", action="store_true")
    view = sub.add_parser("inspect")
    view.add_argument("state", type=Path)
    view.add_argument("--version", type=int)
    args = parser.parse_args()
    try:
        if args.action == "apply":
            out = apply(args.source, args.state, args.upto,
                        args.crash_before_publish)
        else:
            out = inspect(args.state, args.version)
    except ConflictError as exc:
        print("error: %s" % exc, file=sys.stderr)
        sys.exit(2)
    print(json.dumps(out, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()
