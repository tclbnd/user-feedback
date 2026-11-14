"""设备用量明细分批归集。

用法: python3 ingest.py input.json state.json

合并跨批明细(同一 event_id 取最大 (rev, source 优先级),primary 高于
secondary),按 UTC 日与设备维护汇总,逐批输出受影响分区,并把有效明细、
汇总、对账事件与历史版本作为一次原子更新发布。输入存在字段不合法或
同 (event_id, rev, source) 内容冲突时,以退出码 2 拒绝且不改写状态。
"""

import json
import os
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

FIELDS = ("event_id", "rev", "source", "day", "device", "units", "op")
SOURCE_RANK = {"primary": 1, "secondary": 0}
OPS = ("upsert", "delete")
DAY_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


class Reject(Exception):
    """输入不合法或版本冲突,整次调用以退出码 2 拒绝。"""


def _is_int(value):
    return isinstance(value, int) and not isinstance(value, bool)


def normalize_row(row, where):
    """校验字段并返回字段顺序规范的行;不合法则抛 Reject。"""
    if not isinstance(row, dict):
        raise Reject(where + ": 行必须是 JSON 对象")
    if set(row) != set(FIELDS):
        raise Reject(where + ": 字段必须恰好为 " + repr(list(FIELDS)))
    if not isinstance(row["event_id"], str) or not row["event_id"]:
        raise Reject(where + ": event_id 必须是非空字符串")
    if not _is_int(row["rev"]) or row["rev"] < 0:
        raise Reject(where + ": rev 必须是非负整数")
    if row["source"] not in SOURCE_RANK:
        raise Reject(where + ": source 必须是 primary 或 secondary")
    day = row["day"]
    if not isinstance(day, str) or not DAY_RE.fullmatch(day):
        raise Reject(where + ": day 必须是 YYYY-MM-DD")
    try:
        date.fromisoformat(day)
    except ValueError:
        raise Reject(where + ": day 不是有效日期: " + repr(day))
    if not isinstance(row["device"], str) or not row["device"]:
        raise Reject(where + ": device 必须是非空字符串")
    if not _is_int(row["units"]) or row["units"] < 0:
        raise Reject(where + ": units 必须是非负整数")
    if row["op"] not in OPS:
        raise Reject(where + ": op 必须是 upsert 或 delete")
    return {key: row[key] for key in FIELDS}


def load_batches(input_path):
    try:
        request = json.loads(Path(input_path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise Reject("无法读取输入: " + str(exc))
    if not isinstance(request, dict) or not isinstance(request.get("batches"), list):
        raise Reject("输入必须是包含 batches 数组的对象")
    batches = []
    for bi, batch in enumerate(request["batches"]):
        if not isinstance(batch, list):
            raise Reject("批次 %d 必须是数组" % bi)
        batches.append([
            normalize_row(row, "批次 %d 行 %d" % (bi, ri))
            for ri, row in enumerate(batch)
        ])
    return batches


def load_seen(state_path):
    path = Path(state_path)
    if not path.exists():
        return {}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        seen = state["seen"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise Reject("无法读取状态文件: " + str(exc))
    by_event = {}
    for row in seen:
        by_event.setdefault(row["event_id"], {})[
            (row["rev"], row["source"])] = row
    return by_event


def winner_of(versions):
    return max(versions.values(),
               key=lambda row: (row["rev"], SOURCE_RANK[row["source"]]))


def build_totals(rows):
    totals = {}
    for row in rows:
        device = totals.setdefault(row["day"], {}).setdefault(
            row["device"], {"units": 0, "count": 0})
        device["units"] += row["units"]
        device["count"] += 1
    return {day: {dev: totals[day][dev] for dev in sorted(totals[day])}
            for day in sorted(totals)}


def write_state_atomic(state_path, result):
    path = Path(state_path)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent or "."),
                               prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(result, ensure_ascii=False) + "\n")
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def main(input_path, state_path):
    batches = load_batches(input_path)
    by_event = load_seen(state_path)

    # 先校验全部批次与历史版本及本次输入内部版本的冲突;
    # 任何冲突都拒绝整次调用。校验用索引用后即弃,不影响逐批应用。
    check_index = {eid: dict(versions) for eid, versions in by_event.items()}
    for bi, batch in enumerate(batches):
        for ri, row in enumerate(batch):
            key = (row["rev"], row["source"])
            versions = check_index.setdefault(row["event_id"], {})
            existing = versions.get(key)
            if existing is not None and existing != row:
                raise Reject(
                    "批次 %d 行 %d: 与已见版本冲突 event_id=%r rev=%r "
                    "source=%r" % (bi, ri, row["event_id"], row["rev"],
                                   row["source"]))
            versions[key] = row

    winners = {eid: winner_of(versions)
               for eid, versions in by_event.items()}
    changes = []
    for batch in batches:
        affected = set()
        for row in batch:
            by_event.setdefault(row["event_id"], {})[
                (row["rev"], row["source"])] = row
            affected.add(row["event_id"])
        days = set()
        for eid in affected:
            new = winner_of(by_event[eid])
            old = winners.get(eid)
            if old != new:
                if old is not None and old["op"] == "upsert":
                    days.add(old["day"])
                if new["op"] == "upsert":
                    days.add(new["day"])
                winners[eid] = new
        changes.append({"days": sorted(days)})

    rows = sorted((row for row in winners.values() if row["op"] == "upsert"),
                  key=lambda row: row["event_id"])
    seen = sorted((row for versions in by_event.values()
                   for row in versions.values()),
                  key=lambda row: (row["event_id"], row["rev"],
                                   row["source"]))
    result = {
        "rows": rows,
        "totals": build_totals(rows),
        "changes": changes,
        "seen": seen,
    }
    write_state_atomic(state_path, result)


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("用法: python3 ingest.py input.json state.json",
              file=sys.stderr)
        sys.exit(2)
    try:
        main(sys.argv[1], sys.argv[2])
    except Reject as exc:
        print("ingest: 拒绝: " + str(exc), file=sys.stderr)
        sys.exit(2)
