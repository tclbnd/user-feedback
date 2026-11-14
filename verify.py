#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验收程序：乱序注入 + 3 种写入顺序 + 逐时刻离线真值比对 + 跨顺序摘要一致性。

运行：python3 verify.py
退出码 0 表示全部断言通过。
"""
import json
import random
from collections import defaultdict

from tsdb import Engine

WATERMARK = 30
LEVELS = [12, 40]
KEYS = ["a", "b", "c", "d"]
QUERIES = [
    {"at": 490}, {"at": 520}, {"at": 550}, {"at": 600},
    {"at": 520, "key": "a"}, {"at": 550, "key": "c"},
    {"at": 10, "key": "b"}, {"at": 600, "key": "d"},
]


# ---------------------------------------------------------------- 乱序流生成
def build_points():
    """固定点多重集；含同 (key,ts) 多版本、等 ver 重复点与一批超水位迟到点。"""
    regular, late = [], []

    # 固定前缀（三种写入顺序都保留），按时间单调递增插入：
    # 每点 ts >= 当时 max_ts - watermark，4 个锚点全部按常规接纳；
    # 末点 520 把运行水位稳定在 490，ts<=60 的迟到点此后恒进补偿通道。
    # 主体常规点放在宽度 15 < watermark(30) 的窗口 510..525 内：任意两点的
    # 时间差都小于水位，主体无论按何种顺序写入都不会被彼此判迟到，
    # 「常规/迟到」分类只取决于点多重集 -> 跨写入顺序完全一致。
    prefix = [
        {"ts": 50, "key": "a", "v": 1, "ver": 1},
        {"ts": 70, "key": "b", "v": 1, "ver": 1},
        {"ts": 500, "key": "a", "v": 2, "ver": 2},
        {"ts": 520, "key": "b", "v": 2, "ver": 2},
    ]
    regular += prefix
    rng = random.Random(20261114)
    for slot in (510, 515, 520, 525):
        for k in KEYS:
            nver = 3 + rng.randrange(2)          # 3 或 4 个版本
            for ver in range(1, nver + 1):
                regular.append({"ts": slot, "key": k, "v": slot * 100 + ver, "ver": ver})
            if rng.random() < 0.5:              # 等 ver 重复点（值一致，语义等价）
                regular.append({"ts": slot, "key": k, "v": slot * 100 + nver, "ver": nver})
    for slot in (0, 20, 40, 60):
        for k in ("a", "b", "c"):
            for ver in (1, 2):
                late.append({"ts": slot, "key": k, "v": -(slot + ver), "ver": ver})
    return prefix, regular, late


def write_orders(prefix, regular, late):
    body = regular[len(prefix):] + late
    rng = random.Random(42)
    s1 = list(body)                              # 原生成序
    s2 = body[:]
    rng.shuffle(s2)                              # 随机洗牌序
    s3 = sorted(body, key=lambda p: (-p["ts"], p["key"], p["ver"]))  # 时间倒序
    return [("origin", prefix + s1), ("shuffle", prefix + s2), ("ts-desc", prefix + s3)]


# ---------------------------------------------------------------- 离线真值模型
class OfflineTruth:
    """独立于引擎的朴素实现：按到达逐点维护，查询即全量过滤+时间序排序。"""

    def __init__(self, watermark):
        self.wm = watermark
        self.max_ts = None
        self.regular = []                       # 已接纳的原始点（含重复版本）
        self.late = []
        self.winner = {}                        # (key,ts) -> 获胜点 dict
        self.classified_late_keys = set()

    def admit(self, p, seq):
        if self.max_ts is None:
            self.max_ts = p["ts"]
        self.max_ts = max(self.max_ts, p["ts"])
        rec = dict(p)
        rec["seq"] = seq
        key = (p["key"], p["ts"])
        if p["ts"] < self.max_ts - self.wm:
            self.late.append(rec)
            self.classified_late_keys.add(key)
        else:
            self.regular.append(rec)
        cur = self.winner.get(key)
        if cur is None or (p["ver"], seq) > (cur["ver"], cur["seq"]):
            self.winner[key] = rec

    def scan(self, at, key=None):
        out = {}
        for p in self.regular:
            if p["ts"] <= at and (key is None or p["key"] == key):
                k = (p["key"], p["ts"])
                cur = out.get(k)
                if cur is None or (p["ver"], p["seq"]) > (cur["ver"], cur["seq"]):
                    out[k] = p
        res = [{"ts": p["ts"], "key": p["key"], "v": p["v"], "ver": p["ver"]}
               for p in out.values()]
        res.sort(key=lambda d: (d["ts"], d["key"], -d["ver"]))
        return res

    def late_view(self):
        out = {}
        for p in self.late:
            k = (p["key"], p["ts"])
            cur = out.get(k)
            if cur is None or (p["ver"], p["seq"]) > (cur["ver"], cur["seq"]):
                out[k] = p
        return sorted(({"ts": p["ts"], "key": p["key"], "v": p["v"], "ver": p["ver"]}
                       for p in out.values()),
                      key=lambda d: (d["key"], d["ts"], -d["ver"]))


# ---------------------------------------------------------------- 单顺序验收
def run_order(name, stream, n_regular, n_late, report):
    eng = Engine(WATERMARK, LEVELS)
    truth = OfflineTruth(WATERMARK)
    checkpoints, failures = [], 0
    seen_sample = []
    every = 10

    for seq, p in enumerate(stream):
        verdict = eng.insert(p)
        truth.admit(p, seq)
        if seq < 4:                          # 4 个前缀锚点必须全部按常规接纳
            assert verdict == "regular"
        seen_sample.append((p["key"], p["ts"]))

        if (seq + 1) % every == 0 or seq == len(stream) - 1:
            snap = eng.snapshot()
            # 1) 每个查询与离线真值逐点一致
            for q in QUERIES:
                got = eng.scan(q["at"], q.get("key"))
                exp = truth.scan(q["at"], q.get("key"))
                if got != exp:
                    failures += 1
                    report.append(f"  [FAIL] {name} n={seq+1} q={q}")
                # 无重复无缺失：键唯一且计数吻合
                keys = [(d["key"], d["ts"]) for d in got]
                if len(keys) != len(set(keys)) or len(got) != len(exp):
                    failures += 1
            # 2) 单点查询含补偿通道，与真值获胜版本一致
            for k, ts in seen_sample[::17]:
                g = eng.get(k, ts)
                w = truth.winner[(k, ts)]
                exp_one = {"ts": w["ts"], "key": w["key"], "v": w["v"], "ver": w["ver"]}
                if g != exp_one:
                    failures += 1
            # 3) 补偿通道与离线迟到集合一致（单独统计；含缓冲内未密封点的统一去重视图）
            late_got = eng.normalized()["late"]
            if late_got != truth.late_view():
                failures += 1
                report.append(f"  [FAIL] {name} n={seq+1} 补偿通道与离线迟到集不一致")
            checkpoints.append((seq + 1, dict(snap)))

    digest_before = eng.digest()
    eng.finalize()
    final_snap = eng.snapshot()
    digest_after = eng.digest()

    # 终态再全量比对一次（含 include_late 全通道扫描）
    for q in QUERIES:
        assert eng.scan(q["at"], q.get("key")) == truth.scan(q["at"], q.get("key"))
    full = eng.scan(10 ** 9, include_late=True)
    exp_full = sorted(truth.winner.values(),
                      key=lambda d: (d["ts"], d["key"], -d["ver"]))
    exp_full = [{"ts": d["ts"], "key": d["key"], "v": d["v"], "ver": d["ver"]}
                for d in exp_full]
    if full != exp_full:
        failures += 1
        report.append(f"  [FAIL] {name} 全通道终态合并与离线不一致")

    norm = eng.normalized()
    assert digest_before == digest_after, "定型前后内容摘要必须一致"
    assert len(norm["regular"]) + len(norm["late"]) == len(exp_full)
    assert len({(d["key"], d["ts"]) for d in full}) == len(full), "跨层合并存在重复键"
    assert eng.stats["in_late"] == n_late and eng.stats["in_regular"] == n_regular

    return {
        "name": name,
        "checkpoints": checkpoints,
        "final": final_snap,
        "digest": digest_after,
        "stats": dict(eng.stats),
        "normalized": norm,
        "failures": failures,
    }


def main():
    prefix, regular, late = build_points()
    n_regular, n_late = len(regular), len(late)
    total = n_regular + n_late
    report = []
    results = [run_order(name, stream, n_regular, n_late, report)
               for name, stream in write_orders(prefix, regular, late)]

    lines = []
    lines.append("=" * 72)
    lines.append("乱序时序分层缓冲验收报告  (watermark=%d, levels=%s, 点数=%d: 常规%d/迟到%d)"
                 % (WATERMARK, LEVELS, total, n_regular, n_late))
    lines.append("=" * 72)

    # ---- 逐层大小演进（展示一种顺序，另两种终态对齐） ----
    base = results[0]
    lines.append("\n[1] 逐层大小演进（顺序=%s，每 %d 点一个检查点；含 run 数）"
                 % (base["name"], 10))
    lines.append("  n    L0   L1   L2  late | runs(L0/L1/L2/late)")
    for n, s in base["checkpoints"]:
        r = s["runs"]
        lines.append(" %3d  %3d  %3d  %3d  %3d |  %d/%d/%d/%d"
                     % (n, s["L0"], s["L1"], s["L2"], s["late"],
                        r["L0"], r["L1"], r["L2"], r["late"]))

    # ---- 终态各层摘要与压实统计 ----
    lines.append("\n[2] 终态各层摘要与压实统计（finalize 大压实之后）")
    lines.append("  order     L0   L1   L2  late | 摘要(sha256:16)")
    for r in results:
        f = r["final"]
        lines.append("  %-8s  %3d  %3d  %3d  %3d | %s"
                     % (r["name"], f["L0"], f["L1"], f["L2"], f["late"], r["digest"]))
    lines.append("\n  压实明细（L0->L1 / L1->L2 / L2大压实 | 补偿密封/大压实 | 去重点数/迟到去重）")
    for r in results:
        s = r["stats"]
        lines.append("  %-8s   %d/%d/%d | %d/%d | %d/%d"
                     % (r["name"], s["L0->L1"], s["L1->L2"], s["L2_major"],
                        s["late_seal"], s["late_major"],
                        s["dup_removed"], s["late_dup_removed"]))

    # ---- 跨写入顺序一致性 ----
    d0 = results[0]["digest"]
    size_keys = ("L0", "L1", "L2", "late")
    same_digest = all(r["digest"] == d0 for r in results)
    same_layers = all(tuple(r["final"][k] for k in size_keys)
                      == tuple(results[0]["final"][k] for k in size_keys)
                      for r in results)
    same_norm = all(r["normalized"] == results[0]["normalized"] for r in results)
    lines.append("\n[3] 三种写入顺序一致性")
    lines.append("  摘要一致: %s | 各层点数(L0/L1/L2/late)一致: %s | 规范化点集一致: %s"
                 % (same_digest, same_layers, same_norm))
    lines.append("  注: run 数与压实次数随到达时机而变（LSM 正常现象），终态大压实后布局一致。")

    # ---- 补偿通道单独统计 ----
    s0 = results[0]["stats"]
    lines.append("\n[4] 补偿通道（迟到超水位，独立统计、不参与常规查询）")
    lines.append("  迟到注入=%d  去重后键数=%d  迟到点内去重=%d  常规查询命中迟到点=0(断言)"
                 % (s0["in_late"], len(results[0]["normalized"]["late"]),
                    s0["late_dup_removed"]))

    # ---- 一致性证明 ----
    lines.append("\n[5] 查询一致性证明（三个不变量）")
    lines.append("  I1 不可变性: L0 仅在超容时整体排序密封为不可变 run，run 一旦生成只被")
    lines.append("     归并、从不原地改写；迟到点只进补偿通道，永不回灌常规层。")
    lines.append("  I2 归并结合律: merge_runs 对同 (key,ts) 取 (ver,seq) 最大者，归约结果")
    lines.append("     仅由点多重集决定，与 run 划分及压实时机无关 -> 无重复、无覆盖错误。")
    lines.append("  I3 真值等价: 每检查点 scan/get 与独立离线模型逐点比对，全通道终态合并")
    lines.append("     与离线全排序相等；三顺序摘要相同 -> 写入顺序变化不影响查询结果。")

    total_fail = sum(r["failures"] for r in results)
    lines.append("\n[6] 验收结论: %d 次逐点比对失败，断言全部通过=%s"
                 % (total_fail, total_fail == 0))
    text = "\n".join(lines)
    print(text)
    with open("verify_report.txt", "w", encoding="utf-8") as f:
        f.write(text + "\n")

    assert total_fail == 0 and report == []
    assert same_digest and same_layers and same_norm
    print("\nALL CHECKS PASSED")


if __name__ == "__main__":
    main()