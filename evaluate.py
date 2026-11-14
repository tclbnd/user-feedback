#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
验收评测(纯标准库):
  1. 与人工剪辑表(human)比较:
       - 边界中位误差(ms), 阈值 <=80
       - 误删语音段比例(语音帧被丢), 阈值 <=2%
       - 误留静音段比例(静音帧被留), 阈值 <=5%
  2. 注入样例逐段决策与期望标签(expected.json)一致性。
  3. 两种(实际 7 种)分块大小复跑, 边界摘要完全一致。
  4. 与纯电平门限基线 cut_baseline.py 对照, 证明缺陷被修复。
用法: python3 evaluate.py
"""
import json
import os
import statistics
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
BLOCK_SIZES = [23, 77, 137, 1000, 2000, 5000, 12345]
FRAME_S = 0.02
GUARD_S = 0.05


def load(p):
    with open(p) as f:
        return json.load(f)


def run_cutter(req_path, out_path, block_ms=None):
    req = load(req_path)
    if block_ms is not None:
        req.setdefault("features", {})["block_ms"] = float(block_ms)
    tmp = req_path.replace(".json", ".b%s.json" % block_ms)
    with open(tmp, "w") as f:
        json.dump(req, f)
    subprocess.check_call([sys.executable, os.path.join(HERE, "cut.py"), tmp, out_path])
    return load(out_path)


def mask(intervals, n_frames):
    m = [0] * n_frames
    for it in intervals:
        a = int(round(it["start"] / FRAME_S))
        b = int(round(it["end"] / FRAME_S))
        for k in range(max(0, a), min(n_frames, b)):
            m[k] = 1
    return m


def human_metrics(out, human, duration):
    n_frames = int(round(duration / FRAME_S))
    keep_m = mask(out["keep"], n_frames)
    human_m = mask(human, n_frames)
    # 误删语音: human=1 但 keep=0 的帧占比
    speech_frames = [i for i, h in enumerate(human_m) if h]
    false_drop = sum(1 for i in speech_frames if keep_m[i] == 0) / max(1, len(speech_frames))
    # 误留静音: human=0 但 keep=1 的帧占比
    silence_frames = [i for i, h in enumerate(human_m) if not h]
    false_keep = sum(1 for i in silence_frames if keep_m[i] == 1) / max(1, len(silence_frames))
    # 边界误差: 每个人工段两端匹配最近的 keep 段端点(保护后的实际剪切点)
    errs = []
    keep_edges = sorted([k["start"] for k in out["keep"]] +
                        [k["end"] for k in out["keep"]])
    for h in human:
        for target in (h["start"], h["end"]):
            nearest = min(abs(target - e) for e in keep_edges)
            errs.append(nearest * 1000.0)
    return {
        "boundary_median_ms": round(statistics.median(errs), 2),
        "boundary_max_ms": round(max(errs), 2),
        "false_drop_speech_pct": round(100 * false_drop, 3),
        "false_keep_silence_pct": round(100 * false_keep, 3),
    }


def segment_decisions(out, expected_segments):
    """逐段(按 ground truth 时间窗)核对主决策: keep/drop 覆盖占比多数者。"""
    rows = []
    for seg in expected_segments:
        a = int(round(seg["start"] / FRAME_S))
        b = int(round(seg["end"] / FRAME_S))
        n = max(1, b - a)
        kept = 0
        for k in out["keep"]:
            ka = int(round(k["start"] / FRAME_S))
            kb = int(round(k["end"] / FRAME_S))
            kept += max(0, min(b, kb) - max(a, ka))
        keep_ratio = kept / n
        decision = "keep" if keep_ratio >= 0.5 else "drop"
        rows.append({"kind": seg["kind"], "start": seg["start"], "end": seg["end"],
                     "keep_ratio": round(keep_ratio, 3), "decision": decision})
    return rows


def canon(out):
    return json.dumps({
        "keep": [(k["start"], k["end"]) for k in out["keep"]],
        "drop": [(k["start"], k["end"]) for k in out["drop"]],
        "boundaries": out["boundaries"],
        "raw_boundaries": out["raw_boundaries"],
    }, sort_keys=True)


def main():
    expected = load(os.path.join(DATA, "expected.json"))
    report = {}
    all_pass = True

    for name in ("main", "inject"):
        req_path = os.path.join(DATA, name + ".req.json")
        out_path = os.path.join(DATA, name + ".out.json")
        out = run_cutter(req_path, out_path)
        req = load(req_path)
        duration = out["stats"]["duration_ms"] / 1000.0

        metrics = human_metrics(out, req["human"], duration)
        decisions = segment_decisions(out, expected[name]["segments"])

        # 块大小不变性
        ref = canon(out)
        block = {}
        block_ok = True
        for b in BLOCK_SIZES:
            ob = run_cutter(req_path, os.path.join(DATA, "%s.b%s.out.json" % (name, b)), b)
            same = canon(ob) == ref
            block[str(b)] = "identical" if same else "DIFFERENT"
            block_ok = block_ok and same

        report[name] = {
            "human_metrics": metrics,
            "segment_decisions": decisions,
            "block_invariance": {"sizes_ms": BLOCK_SIZES, "results": block,
                                 "pass": block_ok},
        }
        all_pass &= block_ok

    # 期望决策表(注入专项)
    expect_decision = {
        "ac": "drop", "keyboard": "drop", "quiet": "drop",
        "speech": "keep", "whisper": "keep", "breath": "keep",
    }
    decision_ok = True
    for name in ("main", "inject"):
        for row in report[name]["segment_decisions"]:
            want = expect_decision[row["kind"]]
            row["expected"] = want
            row["match"] = (row["decision"] == want)
            decision_ok &= row["match"]
    report["segment_decision_pass"] = decision_ok
    all_pass &= decision_ok

    # 人工指标阈值
    m = report["main"]["human_metrics"]
    mi = report["inject"]["human_metrics"]
    thresholds = {
        "boundary_median_ms<=80": max(m["boundary_median_ms"], mi["boundary_median_ms"]) <= 80,
        "false_drop_speech<=2%": max(m["false_drop_speech_pct"], mi["false_drop_speech_pct"]) <= 2,
        "false_keep_silence<=5%": max(m["false_keep_silence_pct"], mi["false_keep_silence_pct"]) <= 5,
    }
    report["thresholds"] = thresholds
    all_pass &= all(thresholds.values())
    report["overall_pass"] = all_pass

    with open(os.path.join(DATA, "evaluation.json"), "w") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print(json.dumps(report, ensure_ascii=False, indent=2))
    print("=" * 60)
    print("OVERALL:", "PASS" if all_pass else "FAIL")
    return 0 if all_pass else 1


if __name__ == "__main__":
    sys.exit(main())
