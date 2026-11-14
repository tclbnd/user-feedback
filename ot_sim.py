import json
import sys
from pathlib import Path

# 参与冲突判定的字段；缺失按 None 处理
KEYS = ("id", "op", "source", "seq", "pos", "text", "len")


def canon(ev):
    return {k: ev.get(k) for k in KEYS}


def ins_key(ev):
    # 同坐标插入：数据源编号降序、源内序号升序、id 升序稳定落位
    return (ev["pos"], -ev["source"], ev["seq"], ev["id"])


def validate(ev, n):
    pos = ev["pos"]
    if ev["op"] == "ins":
        if not (0 <= pos <= n):
            raise ValueError("insert pos out of range: %r" % (ev,))
    elif ev["op"] == "del":
        if not (0 <= pos and ev["len"] >= 0 and pos + ev["len"] <= n):
            raise ValueError("delete range out of bounds: %r" % (ev,))
    else:
        raise ValueError("unknown op: %r" % (ev,))


def collect(data):
    """跨批次按 id 去重；同 id 字段冲突时退出 2。返回 (unique_by_id, copies, total)。"""
    unique = {}
    copies = {}
    total = 0
    for batch in data["batches"]:
        for ev in batch:
            total += 1
            eid = ev["id"]
            c = canon(ev)
            if eid in unique:
                if unique[eid] != c:
                    print("conflicting event id: %s" % eid, file=sys.stderr)
                    sys.exit(2)
                copies[eid] += 1
            else:
                unique[eid] = c
                copies[eid] = 1
    return unique, copies, total


def replay(doc, events):
    """所有坐标均以原始快照为准：合并重叠删除，插入文本先于该坐标原字符且始终保留。"""
    n = len(doc)
    for ev in events:
        validate(ev, n)
    inserts = sorted((e for e in events if e["op"] == "ins"), key=ins_key)
    deleted = [False] * n
    for e in events:
        if e["op"] == "del":
            for i in range(e["pos"], e["pos"] + e["len"]):
                deleted[i] = True  # 每个原字符最多被删一次，重叠区间自然合并
    ins_at = {}
    for e in inserts:
        ins_at.setdefault(e["pos"], []).append(e["text"])
    parts = []
    for i, ch in enumerate(doc):
        parts.extend(ins_at.get(i, ()))
        if not deleted[i]:
            parts.append(ch)
    parts.extend(ins_at.get(n, ()))
    return "".join(parts)


def audit_delete_outcomes(events):
    """按 (pos, -source, seq, id) 处理删除：首次覆盖任一原字符为 applied，否则 merged。"""
    dels = sorted((e for e in events if e["op"] == "del"), key=ins_key)
    covered = set()
    outcome = {}
    for e in dels:
        rng = range(e["pos"], e["pos"] + e["len"])
        if any(i not in covered for i in rng):
            outcome[e["id"]] = "applied"
            covered.update(rng)
        else:
            outcome[e["id"]] = "merged"
    return outcome


def main(input_path, output_path):
    data = json.loads(Path(input_path).read_text())
    doc = data["doc"]
    unique, copies, total = collect(data)

    # 每种到达顺序独立重放：按该顺序收集去重后的唯一事件，再以原坐标重建
    finals = []
    for order in data["orders"]:
        seen = {}
        for batch in order:
            for ev in data["batches"][batch]:
                seen.setdefault(ev["id"], canon(ev))
        finals.append(replay(doc, list(seen.values())))

    del_outcome = audit_delete_outcomes(unique.values())
    audit = []
    for eid in sorted(unique):
        ev = unique[eid]
        outcome = "applied" if ev["op"] == "ins" else del_outcome[eid]
        audit.append(dict(id=eid, op=ev["op"], copies=copies[eid], outcome=outcome))

    result = dict(
        final=finals,
        converged=len(set(finals)) == 1,
        dedupe=dict(unique=len(unique), duplicates=total - len(unique)),
        audit=audit,
    )
    Path(output_path).write_text(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:])
