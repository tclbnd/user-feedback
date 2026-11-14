import json
import sys
from pathlib import Path


class InvalidInput(Exception):
    pass


def _is_nonneg_int(value):
    # bool 是 int 的子类，按无效处理；只接受真正的非负整数
    return isinstance(value, int) and not isinstance(value, bool) and value >= 0


def _is_nonempty_str(value):
    return isinstance(value, str) and len(value) > 0


def validate(data):
    if not isinstance(data, dict):
        raise InvalidInput("top level must be an object")
    if not _is_nonneg_int(data.get("window")) or data["window"] <= 0:
        raise InvalidInput("window must be a positive integer")
    batches = data.get("batches")
    if not isinstance(batches, list):
        raise InvalidInput("batches must be a list")
    seen = {}
    for bi, batch in enumerate(batches):
        if not isinstance(batch, list):
            raise InvalidInput("batch %d must be a list" % bi)
        for ei, event in enumerate(batch):
            where = "batch %d event %d" % (bi, ei)
            if not isinstance(event, dict):
                raise InvalidInput("%s must be an object" % where)
            for field in ("id", "tenant"):
                if not _is_nonempty_str(event.get(field)):
                    raise InvalidInput("%s: %s must be a non-empty string" % (where, field))
            for field in ("rev", "time", "value"):
                if not _is_nonneg_int(event.get(field)):
                    raise InvalidInput("%s: %s must be a non-negative integer" % (where, field))
            op = event.get("op")
            if op not in ("upsert", "delete"):
                raise InvalidInput("%s: op must be upsert or delete" % where)
            key = (event["id"], event["rev"])
            signature = (event["tenant"], event["time"], event["value"], op)
            if key in seen and seen[key] != signature:
                raise InvalidInput(
                    "conflicting events for id=%r rev=%d" % (event["id"], event["rev"])
                )
            seen[key] = signature
    return data["window"], batches


def run(window, batches):
    # 事件当前状态：id -> (rev, tenant, time, value, op, 原始事件)
    events = {}
    # 桶状态：(tenant, start) -> [count, sum]
    buckets = {}
    changes = []

    for batch in batches:
        # 批内同 id 只保留最大 rev，保证输出与批内行序无关
        latest = {}
        for event in batch:
            eid = event["id"]
            rev = event["rev"]
            if eid not in latest or rev > latest[eid]["rev"]:
                latest[eid] = event

        touched = {}  # key -> 批前 [count, sum]

        def snapshot(key):
            if key not in touched:
                touched[key] = list(buckets.get(key, [0, 0]))

        def add(tenant, time, value):
            key = (tenant, (time // window) * window)
            snapshot(key)
            stat = buckets.setdefault(key, [0, 0])
            stat[0] += 1
            stat[1] += value

        def remove(tenant, time, value):
            key = (tenant, (time // window) * window)
            snapshot(key)
            stat = buckets[key]
            stat[0] -= 1
            stat[1] -= value
            if stat[0] == 0:
                # 无事件的窗口不输出
                del buckets[key]

        for event in latest.values():
            eid = event["id"]
            rev = event["rev"]
            old = events.get(eid)
            if old is not None and old[0] >= rev:
                # 同版本重放（已校验内容一致）或更旧 rev 迟到：不产生任何变动
                continue
            if old is not None:
                # 迟到更正（rev 严格更大才会走到这里）：仅 upsert 曾计入桶，
                # delete 墓碑没有贡献可撤销
                if old[4] == "upsert":
                    remove(old[1], old[2], old[3])
            if event["op"] == "upsert":
                add(event["tenant"], event["time"], event["value"])
            # delete 胜出：仅保留墓碑，不再计入任何窗口
            events[eid] = (
                rev,
                event["tenant"],
                event["time"],
                event["value"],
                event["op"],
                event,
            )

        changed = []
        for key, before in touched.items():
            after = buckets.get(key, [0, 0])
            if before != after:
                changed.append(key)
        changed.sort()
        changes.append([[tenant, start] for tenant, start in changed])

    rows = [state[5] for state in sorted(events.values(), key=lambda s: s[5]["id"])
            if state[4] == "upsert"]
    bucket_rows = [
        {"tenant": tenant, "start": start, "count": stat[0], "sum": stat[1]}
        for (tenant, start), stat in sorted(buckets.items())
    ]
    return {"rows": rows, "buckets": bucket_rows, "changes": changes}


def main(input_path, output_path):
    try:
        data = json.loads(Path(input_path).read_text())
        window, batches = validate(data)
        result = run(window, batches)
    except (OSError, ValueError, KeyError, InvalidInput):
        # 无效输入：退出 2，保留旧输出
        sys.exit(2)
    Path(output_path).write_text(json.dumps(result))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(2)
    main(sys.argv[1], sys.argv[2])
