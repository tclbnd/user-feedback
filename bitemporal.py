"""双时间（业务时间 valid time + 系统时间 transaction/system time）维表关联引擎。

语义契约（见 README）：
- 事件带 system（获知时间，非负整数）与 version（稳定非空标识），可乱序到达。
- 维表事件 op=upsert|remove，业务区间半开 [from,to)，to=null 表示无上限。
- 事实事件带业务时刻 at，op=delete 表示事实撤销（历史报表中仍保留旧版本）。
- 同 version 同载荷为重复事件，无影响；同 version 不同载荷整批拒绝（退出码 2）。
- 同一 system 时刻对同一键的不同 version，按 version 字典序较大者胜出。

引擎内部把维表维护成双时间单元格（valid 区间 × system 区间），支持：
- 拆分（upsert 与旧区间部分重叠时保留两侧残余）；
- 回收（remove 产生遮蔽墓碑，reason=removed）；
- 撤销（更晚 system 的 upsert 重新覆盖墓碑区间）。
事实关联结果同样按 system 区间维护，修订只重算受影响的实体 × 业务时间范围。
"""
from __future__ import annotations

import argparse
import json
import os
import tempfile
from dataclasses import dataclass, asdict
from typing import Any, Dict, List, Optional, Tuple

INF = 10 ** 18  # 内部用极大整数表示开放上界，序列化时写 null


class ConflictError(ValueError):
    """同 version 不同载荷，整批拒绝。"""


class ValidationError(ValueError):
    """事件字段非法。"""


# ---------------------------------------------------------------- 区间原语
def _is_open(hi: Optional[int]) -> bool:
    return hi is None or hi == INF


def _hi(hi: Optional[int]) -> int:
    return INF if hi is None else hi


def overlaps(a_lo: int, a_hi: Optional[int], b_lo: int, b_hi: Optional[int]) -> bool:
    """半开区间是否相交。"""
    return a_lo < _hi(b_hi) and b_lo < _hi(a_hi)


def contains(lo: int, hi: Optional[int], point: int) -> bool:
    return lo <= point < _hi(hi)


def subtract(lo: int, hi: int, f_lo: int, f_hi: int) -> List[Tuple[int, int]]:
    """[lo,hi) 减去 [f_lo,f_hi)，返回至多两个非空半开区间。"""
    out: List[Tuple[int, int]] = []
    if lo < f_lo:
        out.append((lo, min(hi, f_lo)))
    if hi > f_hi:
        out.append((max(lo, f_hi), hi))
    return [(a, b) for a, b in out if a < b]


# ---------------------------------------------------------------- 数据行
@dataclass
class DimCell:
    """维表双时间单元格：业务区间 [vfrom,vto) × 系统区间 [sfrom,sto)。"""
    entity: str
    vfrom: int
    vto: Optional[int]          # null=开放
    sfrom: int
    sto: Optional[int]          # null=开放
    status: str                 # active | removed
    label: Optional[str]
    version: str


@dataclass
class FactVersion:
    """事实的一个系统时间版本。"""
    id: str
    entity: str
    at: int
    sfrom: int
    sto: Optional[int]
    version: str
    deleted: bool


@dataclass
class Link:
    """事实在某个系统时间段内的关联结果及其版本来源。"""
    id: str
    fact_version: str
    entity: str
    at: int
    sfrom: int
    sto: Optional[int]
    reason: str                 # matched | removed | no_dimension
    dimension_version: Optional[str]
    label: Optional[str]


# ---------------------------------------------------------------- 引擎
class Engine:
    def __init__(self) -> None:
        self.events: List[dict] = []
        self.dims: Dict[str, List[DimCell]] = {}
        self.facts: Dict[str, List[FactVersion]] = {}
        self.links: Dict[str, List[Link]] = {}
        self.changes: List[dict] = []
        self.known_versions: Dict[str, str] = {}

    # ---- 持久化
    def to_dict(self) -> dict:
        return {
            "format": 2,
            "events": self.events,
            "dims": {e: [asdict(c) for c in rows] for e, rows in self.dims.items()},
            "facts": {f: [asdict(v) for v in rows] for f, rows in self.facts.items()},
            "links": {f: [asdict(l) for l in rows] for f, rows in self.links.items()},
            "changes": self.changes,
            "known_versions": self.known_versions,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Engine":
        eng = cls()
        eng.events = list(data.get("events", []))
        eng.dims = {
            e: [DimCell(**c) for c in rows] for e, rows in data.get("dims", {}).items()
        }
        eng.facts = {
            f: [FactVersion(**v) for v in rows]
            for f, rows in data.get("facts", {}).items()
        }
        eng.links = {
            f: [Link(**l) for l in rows] for f, rows in data.get("links", {}).items()
        }
        eng.changes = list(data.get("changes", []))
        eng.known_versions = dict(data.get("known_versions", {}))
        return eng

    def save(self, path: str) -> None:
        """原子发布：先写临时文件再 rename，读到的状态永远是完整的一份。"""
        d = os.path.dirname(os.path.abspath(path)) or "."
        fd, tmp = tempfile.mkstemp(prefix=".state-", suffix=".tmp", dir=d)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(self.to_dict(), fh, ensure_ascii=False, sort_keys=True)
                fh.write("\n")
            os.replace(tmp, path)
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    @classmethod
    def load(cls, path: str) -> "Engine":
        with open(path, "r", encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    # ---- 校验与幂等
    @staticmethod
    def _signature(event: dict) -> str:
        return json.dumps(event, sort_keys=True, ensure_ascii=False)

    def _validate(self, event: dict) -> None:
        t = event.get("type")
        if t not in ("dimension", "fact"):
            raise ValidationError("未知事件类型: %r" % t)
        if not isinstance(event.get("version"), str) or not event["version"]:
            raise ValidationError("version 必须是非空字符串")
        s = event.get("system")
        if not isinstance(s, int) or isinstance(s, bool) or s < 0:
            raise ValidationError("system 必须是非负整数")
        if t == "dimension":
            if not isinstance(event.get("entity"), str) or not event["entity"]:
                raise ValidationError("dimension 缺少 entity")
            f, to = event.get("from"), event.get("to")
            if not isinstance(f, int) or isinstance(f, bool):
                raise ValidationError("from 必须是整数")
            if to is not None and (not isinstance(to, int) or isinstance(to, bool) or to <= f):
                raise ValidationError("半开区间要求 from < to（to=null 表示开放）")
            if event.get("op") not in ("upsert", "remove"):
                raise ValidationError("dimension op 必须是 upsert|remove")
            if event["op"] == "upsert" and not isinstance(event.get("label"), str):
                raise ValidationError("upsert 必须带 label")
        else:
            if not isinstance(event.get("id"), str) or not event["id"]:
                raise ValidationError("fact 缺少 id")
            if not isinstance(event.get("entity"), str) or not event["entity"]:
                raise ValidationError("fact 缺少 entity")
            if not isinstance(event.get("at"), int) or isinstance(event["at"], bool):
                raise ValidationError("fact.at 必须是整数")
            if event.get("op", "upsert") not in ("upsert", "delete"):
                raise ValidationError("fact op 必须是 upsert|delete")

    # ---- 维表双时间修订
    def _apply_dim(self, e: dict) -> None:
        entity, s = e["entity"], e["system"]
        f, t = e["from"], _hi(e.get("to"))
        rows = self.dims.setdefault(entity, [])
        touched = [
            r for r in rows
            if _is_open(r.sto) and overlaps(r.vfrom, r.vto, f, t)
        ]
        for r in touched:
            rows.remove(r)
            # 关闭旧系统时间段；同一 system 刻度内刚生成的行直接替换，不留下零长区间
            if r.sfrom < s:
                rows.append(DimCell(r.entity, r.vfrom, r.vto, r.sfrom, s,
                                    r.status, r.label, r.version))
            # 业务时间上的两侧残余：旧断言在修订范围外继续生效（拆分）
            for lo, hi in subtract(r.vfrom, _hi(r.vto), f, t):
                rows.append(DimCell(entity, lo, None if hi == INF else hi, s, None,
                                    r.status, r.label, r.version))
        # 修订范围本身写入新断言（upsert=有效断言，remove=遮蔽墓碑）
        if e["op"] == "upsert":
            cell = DimCell(entity, f, None if t == INF else t, s, None,
                           "active", e["label"], e["version"])
        else:
            cell = DimCell(entity, f, None if t == INF else t, s, None,
                           "removed", None, e["version"])
        rows.append(cell)
        rows.sort(key=lambda c: (c.sfrom, c.vfrom, c.version))

    # ---- 事实版本维护
    def _apply_fact(self, e: dict) -> None:
        fid, s = e["id"], e["system"]
        rows = self.facts.setdefault(fid, [])
        open_row = next((r for r in rows if _is_open(r.sto)), None)
        if open_row is not None:
            rows.remove(open_row)
            if open_row.sfrom < s:
                open_row.sto = s
                rows.append(open_row)
            # sfrom == s：同刻度旧版本被更大 version 替换，不留零长区间
        rows.append(FactVersion(
            fid, e["entity"], e["at"], s, None, e["version"],
            e.get("op", "upsert") == "delete",
        ))
        rows.sort(key=lambda r: (r.sfrom, r.version))

    # ---- 关联解析（直接读双时间维表，供探针与审计使用）
    def resolve(self, entity: str, at: int, s: int) -> dict:
        cells = [
            c for c in self.dims.get(entity, [])
            if c.sfrom <= s and (c.sto is None or s < c.sto)
            and contains(c.vfrom, c.vto, at)
        ]
        if not cells:
            return {"reason": "no_dimension", "dimension_version": None, "label": None}
        pick = max(cells, key=lambda c: (c.sfrom, c.version))
        if pick.status == "removed":
            return {"reason": "removed", "dimension_version": None, "label": None,
                    "_source_version": pick.version}
        return {"reason": "matched", "dimension_version": pick.version,
                "label": pick.label, "_source_version": pick.version}

    def _open_fact(self, fid: str, s: int) -> Optional[FactVersion]:
        return next(
            (r for r in self.facts.get(fid, [])
             if r.sfrom <= s and (r.sto is None or s < r.sto)),
            None,
        )

    def _relink(self, fid: str, s: int) -> None:
        """按 s 时刻状态重算关联，并在视图未变化时与前段合并（coalesce）。

        关联链的系统时间段只在“关联视图真正变化”时切开，因此结果与事件按多少
        个批次到达无关：同一事实即使在每个刻度都被重算，只要 reason/维表版本
        不变，就始终是同一段；变化明细仍逐次记录在 changes 日志中。
        """
        links = self.links.setdefault(fid, [])
        prev = next((l for l in links if _is_open(l.sto)), None)
        old_view = None
        if prev is not None:
            old_view = (prev.reason, prev.dimension_version)
        fv = self._open_fact(fid, s)
        new_view = None
        new_link = None
        if fv is not None and not fv.deleted:
            hit = self.resolve(fv.entity, fv.at, s)
            new_link = Link(fid, fv.version, fv.entity, fv.at, s, None,
                            hit["reason"], hit["dimension_version"], hit["label"])
            new_view = (new_link.reason, new_link.dimension_version)

        if prev is not None and old_view == new_view and new_link is not None:
            # 视图未变化：保留原开放段（仅同步事实版本/业务时刻），不切新段
            prev.fact_version = new_link.fact_version
            prev.at = new_link.at
            current = prev
        else:
            if prev is not None:
                links.remove(prev)
                if prev.sfrom < s:
                    prev.sto = s
                    links.append(prev)
            if new_link is not None:
                links.append(new_link)
            links.sort(key=lambda l: (l.sfrom, l.fact_version))
            current = new_link
        self.changes.append({
            "system": s, "id": fid,
            "fact_version": fv.version if fv else None,
            "old": None if old_view is None else {"reason": old_view[0],
                                                  "dimension_version": old_view[1]},
            "new": None if new_view is None else {"reason": new_view[0],
                                                  "dimension_version": new_view[1]},
            "changed": old_view != new_view,
            "coalesced": prev is not None and old_view == new_view
                         and new_link is not None,
        })

    # ---- 批量应用（稳定顺序 + 增量重算）
    def apply_batch(self, batch: List[dict]) -> dict:
        for e in batch:
            self._validate(e)
        # 冲突先于任何变更检测：同 version 不同载荷整批拒绝；批内完全重复去重
        fresh: List[dict] = []
        batch_sigs: Dict[str, str] = {}
        for e in batch:
            sig = self._signature(e)
            seen = self.known_versions.get(e["version"])
            if seen is not None:
                if seen != sig:
                    raise ConflictError("version %r 载荷冲突，整批拒绝" % e["version"])
                continue  # 与历史完全重复，无影响
            if e["version"] in batch_sigs:
                if batch_sigs[e["version"]] != sig:
                    raise ConflictError(
                        "version %r 批内载荷冲突，整批拒绝" % e["version"])
                continue  # 批内完全重复，无影响
            batch_sigs[e["version"]] = sig
            fresh.append(e)
        # 稳定全局顺序：(system, version)；到达顺序/分区不影响结果
        fresh.sort(key=lambda e: (e["system"], e["version"]))
        affected: set = set()
        last_tick = None
        for e in fresh:
            self.known_versions[e["version"]] = self._signature(e)
            self.events.append(e)
        by_tick: Dict[int, List[dict]] = {}
        for e in fresh:
            by_tick.setdefault(e["system"], []).append(e)
        for s in sorted(by_tick):
            last_tick = s
            dim_events: List[dict] = []
            for e in by_tick[s]:
                if e["type"] == "dimension":
                    self._apply_dim(e)
                    dim_events.append(e)
                else:
                    self._apply_fact(e)
                    affected.add(e["id"])
            # 维表修订只影响：同实体且业务时刻落在修订区间内的当前有效事实
            for de in dim_events:
                f, t = de["from"], _hi(de.get("to"))
                for fid, frows in self.facts.items():
                    fv = self._open_fact(fid, s)
                    if fv is None or fv.deleted:
                        continue
                    if fv.entity == de["entity"] and f <= fv.at < t:
                        affected.add(fid)
            for fid in sorted(affected):
                self._relink(fid, s)
        return {"accepted": len(fresh), "affected": sorted(affected),
                "last_system": last_tick}

    # ---- 查询接口
    def report(self, asof: int, entity: Optional[str] = None,
               valid_from: Optional[int] = None,
               valid_to: Optional[int] = None) -> dict:
        """系统时间 asof 时刻的报表（含该时刻事件），可再按业务时间范围过滤。"""
        rows = []
        for fid in sorted(self.facts):
            fv = self._open_fact(fid, asof)
            if fv is None or fv.deleted:
                continue
            if entity is not None and fv.entity != entity:
                continue
            if valid_from is not None and fv.at < valid_from:
                continue
            if valid_to is not None and not (fv.at < valid_to):
                continue
            link = next(
                (l for l in self.links.get(fid, [])
                 if l.sfrom <= asof and (l.sto is None or asof < l.sto)),
                None,
            )
            if link is None:  # 防御性兜底：直接从维表解析
                hit = self.resolve(fv.entity, fv.at, asof)
                reason, dv, label = hit["reason"], hit["dimension_version"], hit["label"]
            else:
                reason, dv, label = link.reason, link.dimension_version, link.label
            rows.append({
                "id": fid, "fact_version": fv.version, "entity": fv.entity,
                "at": fv.at, "dimension_version": dv, "label": label, "reason": reason,
            })
        return {"asof": asof, "rows": rows}

    def dim_cells(self, entity: str, at: Optional[int] = None,
                  asof: Optional[int] = None) -> List[dict]:
        """按两个时间维度查询维表双时间单元格（版本来源可追溯）。"""
        out = []
        for c in self.dims.get(entity, []):
            if asof is not None and not (c.sfrom <= asof and (c.sto is None or asof < c.sto)):
                continue
            if at is not None and not contains(c.vfrom, c.vto, at):
                continue
            out.append(asdict(c))
        out.sort(key=lambda c: (c["sfrom"], c["vfrom"], c["version"]))
        return out

    def fact_history(self, fid: str) -> dict:
        return {
            "id": fid,
            "versions": [asdict(v) for v in sorted(self.facts.get(fid, []),
                                                   key=lambda r: (r.sfrom, r.version))],
            "links": [asdict(l) for l in sorted(self.links.get(fid, []),
                                                key=lambda l: (l.sfrom, l.fact_version))],
        }

    def change_log(self, since: Optional[int] = None,
                   until: Optional[int] = None) -> List[dict]:
        return [
            c for c in self.changes
            if (since is None or c["system"] >= since)
            and (until is None or c["system"] <= until)
        ]


# ---------------------------------------------------------------- CLI
def _load(path: str) -> Engine:
    if os.path.exists(path):
        return Engine.load(path)
    return Engine()


def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(description="双时间维表关联引擎")
    sub = p.add_subparsers(dest="op", required=True)

    a = sub.add_parser("apply", help="应用一批事件（原子发布）")
    a.add_argument("state")
    a.add_argument("input")

    q = sub.add_parser("query", help="按系统时间出报表，可按实体/业务时间区间过滤")
    q.add_argument("state")
    q.add_argument("--asof", type=int, required=True)
    q.add_argument("--entity")
    q.add_argument("--valid-from", type=int, dest="valid_from")
    q.add_argument("--valid-to", type=int, dest="valid_to")

    d = sub.add_parser("dim", help="查询维表双时间单元格")
    d.add_argument("state")
    d.add_argument("--entity", required=True)
    d.add_argument("--at", type=int)
    d.add_argument("--asof", type=int)

    fh = sub.add_parser("fact", help="查询事实版本与关联历史")
    fh.add_argument("state")
    fh.add_argument("--id", required=True)

    ch = sub.add_parser("changes", help="查看增量关联变更明细")
    ch.add_argument("state")
    ch.add_argument("--since", type=int)
    ch.add_argument("--until", type=int)

    rb = sub.add_parser("rebuild", help="独立全量重建后出报表（审计用）")
    rb.add_argument("state")
    rb.add_argument("--asof", type=int, required=True)

    args = p.parse_args(argv)
    try:
        if args.op == "apply":
            eng = _load(args.state)
            with open(args.input, "r", encoding="utf-8") as fh:
                batch = json.load(fh)["events"]
            result = eng.apply_batch(batch)
            eng.save(args.state)
        else:
            eng = _load(args.state)
            if args.op == "query":
                result = eng.report(args.asof, args.entity,
                                    args.valid_from, args.valid_to)
            elif args.op == "dim":
                result = {"entity": args.entity,
                          "cells": eng.dim_cells(args.entity, args.at, args.asof)}
            elif args.op == "fact":
                result = eng.fact_history(args.id)
            elif args.op == "changes":
                result = {"changes": eng.change_log(args.since, args.until)}
            else:
                from reference_rebuild import rebuild_report
                result = rebuild_report(eng.events, args.asof)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (ConflictError, ValidationError) as exc:
        print("拒绝: %s" % exc, flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
