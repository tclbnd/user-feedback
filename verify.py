"""ingest.py 验收脚本。

覆盖: >=400 条乱序事件、跨日更正、撤销、同版本异源覆盖、跨批重放、
增量与一次性重算一致、逐批受影响日期与离线差量一致、重复批次为空、
同版本同来源冲突与非法字段以退出码 2 拒绝且状态逐字节不变。
"""

import json
import random
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
INGEST = HERE / "ingest.py"
RANK = {"primary": 1, "secondary": 0}
FAILURES = []


def check(cond, msg):
    print(("PASS " if cond else "FAIL ") + msg)
    if not cond:
        FAILURES.append(msg)


def make_row(eid, rev, source, day, device, units, op):
    return {"event_id": eid, "rev": rev, "source": source, "day": day,
            "device": device, "units": units, "op": op}


def generate():
    """生成确定性乱序事件流,返回打乱后的行列表(>=400 行)。"""
    rng = random.Random(20240115)
    devices = ["dev-%02d" % i for i in range(8)]
    days = ["2024-01-%02d" % d for d in range(1, 11)]
    versions = {}
    rows = []

    def add(row):
        key = (row["event_id"], row["rev"], row["source"])
        assert key not in versions, "生成器自身产生冲突: %r" % (key,)
        versions[key] = row
        rows.append(row)

    def winner(eid):
        cand = [r for k, r in versions.items() if k[0] == eid]
        return max(cand, key=lambda r: (r["rev"], RANK[r["source"]]))

    eids = ["e%04d" % i for i in range(140)]
    for eid in eids:
        add(make_row(eid, 1, rng.choice(["primary", "secondary"]),
                     rng.choice(days), rng.choice(devices),
                     rng.randint(0, 60), "upsert"))

    def other_day(day):
        return rng.choice([d for d in days if d != day])

    pool = eids[:]
    rng.shuffle(pool)
    cross_day, pool = pool[:50], pool[50:]
    same_day, pool = pool[:20], pool[20:]
    dev_only, pool = pool[:15], pool[15:]

    for eid in cross_day:  # 跨日更正 rev2
        w = winner(eid)
        add(make_row(eid, 2, rng.choice(["primary", "secondary"]),
                     other_day(w["day"]), rng.choice(devices),
                     rng.randint(0, 60), "upsert"))
    for eid in same_day:  # 同日数值更正 rev2
        w = winner(eid)
        add(make_row(eid, 2, rng.choice(["primary", "secondary"]),
                     w["day"], w["device"], w["units"] + rng.randint(1, 9),
                     "upsert"))
    for eid in dev_only:  # 跨设备更正 rev2
        w = winner(eid)
        dev = rng.choice([d for d in devices if d != w["device"]])
        add(make_row(eid, 2, rng.choice(["primary", "secondary"]),
                     w["day"], dev, w["units"], "upsert"))

    deleted = rng.sample(eids, 20)
    for eid in deleted:  # 撤销
        w = winner(eid)
        add(make_row(eid, w["rev"] + 1, rng.choice(["primary", "secondary"]),
                     w["day"], w["device"], w["units"], "delete"))
    for eid in deleted[:8]:  # 撤销后再复活,跨日
        w = winner(eid)
        add(make_row(eid, w["rev"] + 1, rng.choice(["primary", "secondary"]),
                     other_day(w["day"]), rng.choice(devices),
                     rng.randint(0, 60), "upsert"))

    keys = list(versions)
    rng.shuffle(keys)
    alt = 0  # 同版本异源覆盖: 内容不同但 primary 恒胜
    for eid, rev, source in keys:
        if alt >= 30:
            break
        other = "secondary" if source == "primary" else "primary"
        if (eid, rev, other) in versions:
            continue
        w = versions[(eid, rev, source)]
        add(make_row(eid, rev, other, other_day(w["day"]),
                     rng.choice(devices), w["units"] + rng.randint(1, 5),
                     w["op"]))
        alt += 1

    for _ in range(130):  # 跨批重复投递(逐字节相同的重放)
        rows.append(dict(rng.choice(rows)))

    rng.shuffle(rows)  # 全局乱序: 更正可能先于原始版本到达
    return rows


def reference(batch_seq):
    """离线重算: 逐批应用,返回 rows/totals/seen 与逐批受影响日期。"""
    seen = {}
    winners = {}
    changes = []
    for batch in batch_seq:
        affected = set()
        for row in batch:
            key = (row["event_id"], row["rev"], row["source"])
            assert key not in seen or seen[key] == row
            seen[key] = row
            affected.add(row["event_id"])
        days = set()
        for eid in affected:
            cand = [r for k, r in seen.items() if k[0] == eid]
            new = max(cand, key=lambda r: (r["rev"], RANK[r["source"]]))
            old = winners.get(eid)
            if old != new:
                if old is not None and old["op"] == "upsert":
                    days.add(old["day"])
                if new["op"] == "upsert":
                    days.add(new["day"])
                winners[eid] = new
        changes.append({"days": sorted(days)})
    rows = sorted((r for r in winners.values() if r["op"] == "upsert"),
                  key=lambda r: r["event_id"])
    totals = {}
    for row in rows:
        leaf = totals.setdefault(row["day"], {}).setdefault(
            row["device"], {"units": 0, "count": 0})
        leaf["units"] += row["units"]
        leaf["count"] += 1
    seen_list = sorted(seen.values(),
                       key=lambda r: (r["event_id"], r["rev"], r["source"]))
    return {"rows": rows, "totals": totals, "changes": changes,
            "seen": seen_list}


def run_cli(batches, state_path, input_path):
    input_path.write_text(json.dumps({"batches": batches}), encoding="utf-8")
    return subprocess.run(
        [sys.executable, str(INGEST), str(input_path), str(state_path)],
        capture_output=True, text=True)


def split_batches(rows, n):
    size = (len(rows) + n - 1) // n
    return [rows[i:i + size] for i in range(0, len(rows), size)]


def main():
    tmp = Path(tempfile.mkdtemp(prefix="ingest-verify-"))
    inp = tmp / "input.json"
    state = tmp / "state.json"

    rows = generate()
    check(len(rows) >= 400, "事件总数 >= 400 (实际 %d)" % len(rows))

    batches = split_batches(rows, 9)
    replay = [dict(r) for r in batches[1]]  # 跨批重放第一批历史批次
    seq = batches + [replay]
    groups = [seq[0:3], seq[3:6], seq[6:10]]  # 三次 CLI 调用

    collected = []
    ok = True
    for group in groups:
        proc = run_cli(group, state, inp)
        if proc.returncode != 0:
            print(proc.stderr)
            ok = False
            break
        collected.extend(json.loads(state.read_text())["changes"])
    check(ok, "三次增量调用均退出 0")

    final = json.loads(state.read_text())
    exp = reference(seq)
    check(final["rows"] == exp["rows"],
          "有效明细与离线重算一致 (%d 行)" % len(exp["rows"]))
    check(final["totals"] == exp["totals"], "逐日设备用量与离线重算一致")
    check(final["seen"] == exp["seen"],
          "历史版本 seen 与离线重算一致 (%d 版本)" % len(exp["seen"]))
    check(collected == exp["changes"], "逐批受影响日期与离线差量一致")
    check(collected[-1] == {"days": []}, "跨批重放的重复批次受影响日期为空")
    check(any(len(c["days"]) > 1 for c in collected),
          "数据确实覆盖跨日更正(单批影响多个日期)")
    n_events = len({r["event_id"] for r in exp["seen"]})
    check(len(final["rows"]) < n_events, "撤销事件不出现在 rows 中")

    empty = [(d, v) for d, devs in final["totals"].items()
             for v in devs.values() if v["count"] < 1]
    check(not empty and all(final["totals"][d] for d in final["totals"]),
          "无空分组")
    total_count = sum(v["count"] for devs in final["totals"].values()
                      for v in devs.values())
    check(total_count == len(final["rows"]), "汇总事件数等于有效明细行数")

    state2 = tmp / "state2.json"
    proc = run_cli(seq, state2, inp)
    oneshot = json.loads(state2.read_text()) if proc.returncode == 0 else {}
    check(proc.returncode == 0
          and oneshot.get("rows") == final["rows"]
          and oneshot.get("totals") == final["totals"]
          and oneshot.get("seen") == final["seen"]
          and oneshot.get("changes") == collected,
          "一次性重放与增量处理结果逐项一致")

    before = state.read_bytes()
    victim = dict(final["seen"][0])
    victim["units"] += 1  # 同 (event_id, rev, source) 内容冲突
    proc = run_cli([[victim]], state, inp)
    check(proc.returncode == 2, "同版本同来源冲突以退出码 2 拒绝")
    check(state.read_bytes() == before, "冲突后状态文件逐字节不变")

    base = dict(final["seen"][1])
    invalid_cases = {
        "rev 为负": lambda r: r.update(rev=-1),
        "units 为布尔": lambda r: r.update(units=True),
        "非法日期": lambda r: r.update(day="2024-02-30"),
        "非法 source": lambda r: r.update(source="prim"),
        "非法 op": lambda r: r.update(op="remove"),
        "空 event_id": lambda r: r.update(event_id=""),
        "多余字段": lambda r: r.update(extra=1),
        "缺少字段": lambda r: r.pop("device"),
    }
    for name, mutate in invalid_cases.items():
        bad = dict(base)
        mutate(bad)
        proc = run_cli([[bad]], state, inp)
        check(proc.returncode == 2 and state.read_bytes() == before,
              "非法字段拒绝且状态不变: " + name)

    good = make_row("e9999", 1, "primary", "2024-01-05", "dev-00", 3, "upsert")
    proc = run_cli([[good, victim]], state, inp)
    check(proc.returncode == 2 and state.read_bytes() == before,
          "含失效批次的调用不改动已有状态")

    state3 = tmp / "state3.json"
    a = make_row("x", 1, "primary", "2024-01-01", "dev-00", 1, "upsert")
    b = dict(a)
    b["units"] = 2
    proc = run_cli([[a, b]], state3, inp)
    check(proc.returncode == 2 and not state3.exists(),
          "单次输入内冲突拒绝且不创建状态文件")

    ghost = make_row("ghost", 1, "primary", "2024-01-01", "dev-00", 0, "delete")
    proc = run_cli([[ghost]], state3, inp)
    s3 = json.loads(state3.read_text())
    check(proc.returncode == 0 and s3["rows"] == [] and s3["totals"] == {}
          and s3["changes"] == [{"days": []}],
          "撤销未知事件合法且无贡献")

    if FAILURES:
        print("\n%d 项失败" % len(FAILURES))
        sys.exit(1)
    print("\n全部通过")


if __name__ == "__main__":
    main()
