"""随机差分测试：随机生成维表/事实修订序列，逐切点比对增量引擎与全量重建。

运行：python3 property_check.py
覆盖随机的区间端点（含开放上界）、upsert/remove、事实补录/撤销/改时刻、
乱序到达、不同批次切分；任何一条事实的维表版本不一致即失败。
"""
from __future__ import annotations

import copy
import random

from bitemporal import Engine
from reference_rebuild import rebuild_report


def random_events(rng: random.Random):
    events = []
    version_seq = 0

    def vtag(prefix: str) -> str:
        nonlocal version_seq
        version_seq += 1
        return "%s-%04d" % (prefix, version_seq)

    entities = ["E1", "E2"]
    # 初始维表：随机若干区间，含一个开放上界
    cursor = 0
    for ent in entities:
        for _ in range(rng.randint(1, 3)):
            width = rng.randint(5, 20)
            events.append({
                "type": "dimension", "entity": ent, "from": cursor,
                "to": cursor + width, "system": 0,
                "version": vtag("d"), "op": "upsert",
                "label": rng.choice(["A", "B", "C"]),
            })
            cursor += width
        events.append({
            "type": "dimension", "entity": ent, "from": cursor, "to": None,
            "system": 0, "version": vtag("d"), "op": "upsert",
            "label": rng.choice(["A", "B", "C"]),
        })
    # 初始事实
    fact_ids = []
    for i in range(rng.randint(6, 12)):
        fid = "F%d" % i
        fact_ids.append(fid)
        events.append({
            "type": "fact", "id": fid, "entity": rng.choice(entities),
            "at": rng.randint(0, 80), "system": 1, "version": vtag("f"),
        })

    # 随机回溯修订
    for tick in range(2, rng.randint(12, 30)):
        kind = rng.random()
        if kind < 0.55:  # 维表 upsert/remove，业务区间一律早于当前 system
            lo = rng.randint(0, 70)
            hi = lo + rng.randint(1, 25)
            events.append({
                "type": "dimension", "entity": rng.choice(entities),
                "from": lo,
                "to": None if rng.random() < 0.15 else hi,
                "system": tick, "version": vtag("d"),
                "op": rng.choice(["upsert", "upsert", "remove"]),
                "label": rng.choice(["A", "B", "C", "X", "Y"]),
            })
        else:  # 事实补录 / 撤销 / 改业务时刻
            fid = rng.choice(fact_ids + ["F-new%d" % rng.randint(0, 3)])
            op = "delete" if rng.random() < 0.2 else "upsert"
            events.append({
                "type": "fact", "id": fid, "entity": rng.choice(entities),
                "at": rng.randint(0, 80), "system": tick,
                "version": vtag("f"), "op": op,
            })
    return events


def row_map(report: dict) -> dict:
    return {r["id"]: r for r in report["rows"]}


def main() -> None:
    rng = random.Random(424242)
    trials = 300
    for trial in range(trials):
        events = random_events(rng)
        max_sys = max(e["system"] for e in events)

        # 三种到达方式：整批、按 1~4 大小切批、完全乱序单批
        ref = Engine()
        ref.apply_batch(copy.deepcopy(events))

        streamed = Engine()
        i = 0
        while i < len(events):
            step = rng.randint(1, 4)
            streamed.apply_batch(copy.deepcopy(events[i:i + step]))
            i += step

        shuffled = Engine()
        shuf = copy.deepcopy(events)
        rng.shuffle(shuf)
        shuffled.apply_batch(shuf)

        for cut in range(0, max_sys + 2):
            expect = row_map(rebuild_report(ref.events, cut))
            for eng in (ref, streamed, shuffled):
                got = row_map(eng.report(cut))
                assert got == expect, (
                    "trial=%d cut=%d 不一致\n引擎=%s\n重建=%s" % (trial, cut, got, expect))
        # 三种方式的双时间结构（除变更日志粒度外）必须一致
        def fp(eng: Engine) -> str:
            import json
            d = eng.to_dict()
            return json.dumps({k: d[k] for k in ("dims", "facts", "links")},
                              sort_keys=True, ensure_ascii=False)
        assert fp(streamed) == fp(ref), "trial=%d 流式结构不一致" % trial
        assert fp(shuffled) == fp(ref), "trial=%d 乱序结构不一致" % trial

    print("OK: %d 个随机序列 × 全部系统切点，增量/流式/乱序 与全量重建完全一致" % trials)


if __name__ == "__main__":
    main()

