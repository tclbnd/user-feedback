"""双时态(业务时间 + 系统时间)维表与事实关联 CLI。

用法:
    python3 bitemporal.py apply state.db batch.json
    python3 bitemporal.py query state.db --asof 9

模型(见 README.md):
* 事件只追加、不物理修改。version 是稳定事件标识: 同 version 完全重复幂等跳过;
  同 version 不同载荷 => 整批拒绝, 退出 2, 状态不变。
* 区间约定: 维度业务区间为半开 [from, to), to=null 表示无上限; 系统时间以
  system <= asof 判定生效(含该时刻)。同 system 多版本覆盖同一业务时刻(或同一
  fact id)时, 按 version 字典序较大者胜出。
* 修订: upsert 抢占重叠区间(旧区间被切分); remove 是对该业务区间的遮蔽(撤销需
  以更晚版本重新 upsert); fact 可用 op=delete 删除, 更晚事件可复活。
* 重算: 只重算本批受影响的行 —— 当前视图中最新版本发生变化的 fact 自身, 以及
  被新增维表事件的实体与业务区间覆盖的当前有效 fact; 不在批次间累积。
* 重放: 事件可乱序、可重复到达; 报表完全由 (system, version) 顺序重放决定,
  与到达批次无关, 因此历史报表(asof)与当前报表均可复现。apply 原子发布
  事件流、当前视图与日志。
"""
import argparse
import json
import os
from pathlib import Path

MATCHED = "matched"
REMOVED = "removed"
NO_DIMENSION = "no_dimension"


class Reject(Exception):
    """输入非法或 version 载荷冲突: 整批拒绝, 退出 2。"""


def _is_int(x):
    return isinstance(x, int) and not isinstance(x, bool)


def _canonical(event):
    return json.dumps(event, sort_keys=True, separators=(",", ":"))


def validate(event):
    if not isinstance(event, dict):
        raise Reject("event must be a JSON object")
    etype = event.get("type")
    if etype not in ("fact", "dimension"):
        raise Reject("unknown event type")
    version = event.get("version")
    if not isinstance(version, str) or not version:
        raise Reject("version must be a non-empty string")
    system = event.get("system")
    if not _is_int(system) or system < 0:
        raise Reject("system must be a non-negative integer")
    if etype == "fact":
        if not isinstance(event.get("id"), str) or not event["id"]:
            raise Reject("fact id must be a non-empty string")
        op = event.get("op")
        if op is not None and op != "delete":
            raise Reject("fact op must be delete")
        if op != "delete":
            if not isinstance(event.get("entity"), str) or not event["entity"]:
                raise Reject("fact entity must be a non-empty string")
            if not _is_int(event.get("at")):
                raise Reject("fact at must be an integer")
    else:
        if not isinstance(event.get("entity"), str) or not event["entity"]:
            raise Reject("dimension entity must be a non-empty string")
        if not _is_int(event.get("from")):
            raise Reject("dimension from must be an integer")
        to = event.get("to")
        if to is not None and (not _is_int(to) or to <= event["from"]):
            raise Reject("dimension to must be null or > from")
        op = event.get("op", "upsert")
        if op not in ("upsert", "remove"):
            raise Reject("dimension op must be upsert or remove")
        if op == "upsert" and not isinstance(event.get("label"), str):
            raise Reject("dimension upsert requires a label")


def dim_timeline(events, entity, asof):
    """实体在 asof 已知的业务时间线。

    按 (system, version) 顺序应用 upsert/remove, 后应用者抢占重叠部分
    (同 system 时 version 大者后应用、因而胜出)。返回互不重叠、按 from
    排序的 (from, to, event) 列表; to 为 None 表示无上限。
    """
    relevant = [e for e in events
                if e["type"] == "dimension" and e["entity"] == entity
                and e["system"] <= asof]
    relevant.sort(key=lambda e: (e["system"], e["version"]))
    timeline = []
    for e in relevant:
        frm, to = e["from"], e["to"]
        nxt = []
        for (a, b, old) in timeline:
            if (b is not None and b <= frm) or (to is not None and a >= to):
                nxt.append((a, b, old))          # 不重叠
                continue
            if a < frm:                          # 左残段
                nxt.append((a, frm, old))
            if to is not None and (b is None or b > to):   # 右残段
                nxt.append((to, b, old))
        nxt.append((frm, to, e))
        timeline = nxt
    timeline.sort(key=lambda iv: iv[0])
    return timeline


def report(events, asof):
    """asof 时刻的报表: 每个 fact id 取 system<=asof 的最新事件(并列取
    version 大者), 被删除的不输出; 每笔有效 fact 按 entity+at 匹配覆盖的
    最新维度区间, 输出匹配版本或无法匹配的理由。"""
    latest = {}
    for e in events:
        if e["type"] != "fact" or e["system"] > asof:
            continue
        key = (e["system"], e["version"])
        cur = latest.get(e["id"])
        if cur is None or key > (cur["system"], cur["version"]):
            latest[e["id"]] = e
    timelines = {}
    rows = []
    for fid in sorted(latest):
        fe = latest[fid]
        if fe.get("op") == "delete":
            continue
        entity = fe["entity"]
        if entity not in timelines:
            timelines[entity] = dim_timeline(events, entity, asof)
        hit = None
        for (a, b, de) in timelines[entity]:
            if a <= fe["at"] and (b is None or fe["at"] < b):
                hit = de
                break
        row = {"id": fid, "fact_version": fe["version"], "entity": entity,
               "at": fe["at"], "dimension_version": None, "label": None}
        if hit is None:
            row["reason"] = NO_DIMENSION
        elif hit.get("op", "upsert") == "remove":
            row["reason"] = REMOVED
        else:
            row.update(dimension_version=hit["version"], label=hit["label"],
                       reason=MATCHED)
        rows.append(row)
    return {"asof": asof, "rows": rows}


def apply_batch(state, batch):
    """应用一批事件, 返回 {"accepted", "affected"}; 失败整批拒绝。

    affected = 本批实际重新求关联的 fact id:
      1) 变化的 fact 自身 —— 新事件成为该 id 在当前视图(截至最新 system)的
         最新版本;
      2) 新增维表事件的实体与业务区间所覆盖的、当前视图中的有效 fact。
    """
    if not isinstance(batch, dict) or not isinstance(batch.get("events"), list):
        raise Reject("input must be an object with an events array")
    for e in batch["events"]:
        validate(e)
    known = {_event_key(e): _canonical(e) for e in state["events"]}
    fresh = []
    for e in batch["events"]:
        c = _canonical(e)
        old = known.get(_event_key(e))
        if old is not None:
            if old != c:
                raise Reject("conflicting payload for version " + str(e.get("version")))
            continue                              # 完全重复: 幂等跳过
        known[_event_key(e)] = c
        fresh.append(e)
    affected = set()
    if fresh:
        events = state["events"] + fresh
        now = max(e["system"] for e in events)
        latest = {}
        for e in events:
            if e["type"] != "fact":
                continue
            key = (e["system"], e["version"])
            cur = latest.get(e["id"])
            if cur is None or key > (cur["system"], cur["version"]):
                latest[e["id"]] = e
        for e in fresh:
            if e["type"] == "fact" and latest.get(e["id"]) is e:
                affected.add(e["id"])
        new_dims = [e for e in fresh if e["type"] == "dimension"]
        if new_dims:
            for row in report(events, now)["rows"]:
                for d in new_dims:
                    if (d["entity"] == row["entity"] and d["from"] <= row["at"]
                            and (d["to"] is None or row["at"] < d["to"])):
                        affected.add(row["id"])
                        break
        state["events"] = events
        state["view"] = report(events, now)       # 当前报表: 截至最新 system
    result = {"accepted": len(fresh), "affected": sorted(affected)}
    state.setdefault("log", []).append(result)
    return result


def _event_key(event):
    return event["version"]


def _save(state, path):
    tmp = path.with_name(path.name + ".tmp")      # 原子发布
    tmp.write_text(json.dumps(state) + "\n")
    os.replace(tmp, path)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="op", required=True)
    for cmd in ("apply", "query"):
        a = sub.add_parser(cmd)
        a.add_argument("state", type=Path)
        if cmd == "apply":
            a.add_argument("input", type=Path)
        else:
            a.add_argument("--asof", type=int, required=True)
    a = p.parse_args(argv)
    try:
        state = (json.loads(a.state.read_text()) if a.state.exists()
                 else {"events": [], "view": None, "log": []})
        state.setdefault("events", [])
        if a.op == "apply":
            result = apply_batch(state, json.loads(a.input.read_text()))
            _save(state, a.state)
        else:
            if a.asof < 0:
                raise Reject("asof must be non-negative")
            result = report(state["events"], a.asof)
        print(json.dumps(result, sort_keys=True))
    except (Reject, KeyError, ValueError, OSError) as e:
        p.exit(2, str(e) + "\n")


if __name__ == "__main__":
    main()
