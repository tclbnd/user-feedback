"""独立全量重建（审计基准）。

不依赖 Engine 的增量状态，仅从原始事件日志出发，用另一套实现思路重建：
1. 按 (system, version) 稳定排序（与到达顺序、分区无关），同 version 去重；
2. 维表：对每个实体维护“业务区间段 -> 各系统时刻断言列表”，查询时折叠出
   asof 时刻覆盖目标业务点的最新断言（同 system 取字典序较大 version）；
3. 事实：折叠出每个 fact id 在 asof 的最新版本；
4. 逐事实重新关联，输出与 Engine.report 完全相同的行结构。

它与 bitemporal.Engine 没有共享代码路径，因此两边一致才能互相证明。
"""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple


def _hi(to: Optional[int]) -> Optional[int]:
    return to  # None 即开放


def _covers(lo: int, hi: Optional[int], point: int) -> bool:
    return lo <= point and (hi is None or point < hi)


def _dedupe(events: List[dict]) -> List[dict]:
    import json
    sigs: Dict[str, str] = {}
    out: List[dict] = []
    for e in events:
        sig = json.dumps(e, sort_keys=True, ensure_ascii=False)
        prev = sigs.get(e["version"])
        if prev is None:
            sigs[e["version"]] = sig
            out.append(e)
        elif prev != sig:  # 正常调用方不会出现：冲突应在入口被拒绝
            raise ValueError("version %r 载荷冲突" % e["version"])
    return out


TBIG = 10 ** 18


def _cut_segments(segs: List[list], f: int, t: Optional[int]) -> List[list]:
    """把与 [f,t) 相交的旧段按全部相关边界重切。

    边界 = 修订端点 ∪ 每个相交旧段的完整端点。重切同时产生：
    - 修订区间内的最小段；
    - 左残余（其右端点可能恰为 f）与右残余（其左端点可能恰为 t）。
    因此这里不能按 lo>=t / hi<=f 跳过任何边界小段，否则会吞掉残余断言。
    每个小段继承完全覆盖它的旧断言；不相交的旧段原样保留。
    """
    tval = TBIG if t is None else t
    overlapping = [s for s in segs if s[0] < tval and (s[1] is None or s[1] > f)]
    untouched = [s for s in segs if s not in overlapping]
    bounds = {f, tval}
    for slo, shi, _ in overlapping:
        bounds.add(slo)
        if shi is not None:
            bounds.add(shi)
    cuts = sorted(bounds)
    rebuilt: List[list] = list(untouched)
    for i in range(len(cuts) - 1):
        lo, hi = cuts[i], cuts[i + 1]
        inherited: List[tuple] = []
        for slo, shi, assertions in overlapping:
            shi_v = TBIG if shi is None else shi
            if slo <= lo and hi <= shi_v:
                inherited = assertions
                break
        # 到达修订右端点且被某个开放旧段覆盖的小段保持开放（开放段的右侧残余）
        is_open = hi == tval and any(
            shi is None and slo <= lo for slo, shi, _ in overlapping)
        rebuilt.append([lo, None if is_open else hi, list(inherited)])
    rebuilt.sort(key=lambda s: (s[0], s[1] if s[1] is not None else TBIG))
    return rebuilt


def rebuild(events: List[dict], asof: int) -> dict:
    events = _dedupe(events)
    visible = [e for e in events if e["system"] <= asof]
    visible.sort(key=lambda e: (e["system"], e["version"]))

    # 维表：实体 -> 业务区间段集合，每段记录系统时间序列上的断言
    # seg: [lo, hi, assertions[(system, version, op, label)]]
    dim_segments: Dict[str, List[list]] = {}

    # 事实：id -> 按系统时间排序的版本列表
    fact_versions: Dict[str, List[dict]] = {}

    for e in visible:
        if e["type"] == "dimension":
            f, t = e["from"], e.get("to")
            entity = e["entity"]
            dim_segments[entity] = _cut_segments(
                dim_segments.setdefault(entity, []), f, t)
            tval = TBIG if t is None else t
            for lo, hi, assertions in dim_segments[entity]:
                hi_v = TBIG if hi is None else hi
                if f <= lo and hi_v <= tval:  # 段完全包含在修订业务区间内
                    assertions.append((e["system"], e["version"],
                                       e["op"], e.get("label")))
        else:
            fact_versions.setdefault(e["id"], []).append(e)

    def resolve_dim(entity: str, at: int) -> Tuple[str, Optional[str], Optional[str]]:
        candidates: List[tuple] = []
        for lo, hi, assertions in dim_segments.get(entity, []):
            if _covers(lo, hi, at):
                candidates.extend(assertions)
        if not candidates:
            return ("no_dimension", None, None)
        system, version, op, label = max(candidates, key=lambda a: (a[0], a[1]))
        if op == "remove":
            return ("removed", None, None)
        return ("matched", version, label)

    rows = []
    for fid in sorted(fact_versions):
        latest = max(fact_versions[fid], key=lambda e: (e["system"], e["version"]))
        if latest.get("op") == "delete":
            continue
        reason, dv, label = resolve_dim(latest["entity"], latest["at"])
        rows.append({
            "id": fid,
            "fact_version": latest["version"],
            "entity": latest["entity"],
            "at": latest["at"],
            "dimension_version": dv,
            "label": label,
            "reason": reason,
        })
    return {"asof": asof, "rows": rows}


def rebuild_report(events: List[dict], asof: int) -> dict:
    return rebuild(events, asof)


def build_segments(events: List[dict], asof: int) -> Dict[str, List[list]]:
    """调试入口：返回 asof 时刻每个实体的业务区间段及断言序列。"""
    events = _dedupe(events)
    visible = sorted((e for e in events if e["system"] <= asof),
                     key=lambda e: (e["system"], e["version"]))
    dim_segments: Dict[str, List[list]] = {}
    for e in visible:
        if e["type"] != "dimension":
            continue
        f, t, entity = e["from"], e.get("to"), e["entity"]
        dim_segments[entity] = _cut_segments(
            dim_segments.setdefault(entity, []), f, t)
        tval = TBIG if t is None else t
        for lo, hi, assertions in dim_segments[entity]:
            hi_v = TBIG if hi is None else hi
            if f <= lo and hi_v <= tval:
                assertions.append((e["system"], e["version"],
                                   e["op"], e.get("label")))
    return dim_segments

