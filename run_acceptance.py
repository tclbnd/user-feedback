"""验收脚本：两个场景的端到端检查。

场景 1：完整 40 分钟素材，检查每 30 s 对应点对齐 p95 <= 5 ms、跨重启
        绝对时间连续、独立脉冲检测复核输出音轨。
场景 2：移除一整段同步标记并注入局部伪峰，确认无覆盖段标记为未校准，
        正常段仍通过 5 ms 误差门槛。
"""
import math
import struct
import sys

sys.path.insert(0, ".")
from clockcal import generator as g
from clockcal import detect, pipeline, media
from clockcal.model import map_tick, REGRESSION

P95_LIMIT_MS = 5.0


def detect_all(sc):
    """从渲染信号检测闪光与声脉冲（含伪峰），返回检测时刻列表。"""
    flashes = detect.detect_flashes(
        [f[1] for f in sc["frames"]],
        g.render_frame_brightness(sc), sc["cam_fps"])
    segs = g.render_audio_segments(sc)
    tones = []
    for s in segs:
        samples = [v[0] / 32767.0
                   for v in struct.iter_unpack("<h", s["pcm_bytes"])]
        peaks = detect.detect_tones(samples, s["local_rate"])
        tones.extend(s["start_tick"] + p for p in peaks)
    return flashes, tones, segs


def checkpoint_epochs(dev, checkpoints):
    """用与校准器一致的回拨规则给检查点分 epoch。"""
    eps = [[]]
    prev = None
    for R in checkpoints:
        t = dev.to_local(R)
        if prev is not None and t < prev - REGRESSION:
            eps.append([])
        eps[-1].append((R, t))
        prev = t
    return eps


def assess_mapping(sc, models):
    """检查每 30 s 对应点的映射误差，返回 (已覆盖误差列表, 未映射列表)。"""
    errs, unmapped = [], []
    for d, dev in (("cam", sc["cam"]), ("mic", sc["mic"])):
        for ep, items in enumerate(checkpoint_epochs(dev, sc["checkpoints_R"])):
            if ep not in models[d]:
                unmapped.extend((d, R) for R, t in items)
                continue
            for R, tick in items:
                Rhat = map_tick(models[d][ep], tick)
                if Rhat is None:
                    unmapped.append((d, R))
                else:
                    errs.append(abs(Rhat - R) * 1000.0)
    return errs, unmapped


def p95(vals):
    s = sorted(vals)
    return s[math.ceil(0.95 * len(s)) - 1]


def scenario_one():
    print("=" * 64)
    print("场景 1：完整 40 分钟素材")
    print("=" * 64)
    sc = g.simulate(seed=2026)
    flashes, tones, segs = detect_all(sc)
    print("检测：闪光 %d、声脉冲 %d（真值事件 %d）" %
          (len(flashes), len(tones), len(sc["events_R"])))
    models, report = pipeline.calibrate(flashes, tones)
    errs, unmapped = assess_mapping(sc, models)
    got_p95 = p95(errs)
    print("对应点：已映射 %d、未映射 %d、p95=%.3f ms、max=%.3f ms" %
          (len(errs), len(unmapped), got_p95, max(errs)))
    assert not unmapped, "完整素材不应有未映射检查点"
    assert got_p95 <= P95_LIMIT_MS, "p95 超过 5 ms"
    # 跨重启连续性
    for d in ("cam", "mic"):
        e0, e1 = models[d][0], models[d][1]
        before = map_tick(e0, e0["coverage"][1])
        after = map_tick(e1, e1["coverage"][0])
        gap = after - before
        print("  %s 重启两侧 R=%.3f -> %.3f，间隔 %.3f s（单调=%s）" %
              (d, before, after, gap, after > before))
        assert after > before and abs(gap - 30.0) < 1.0
    # 独立脉冲复核输出音轨
    out_pcm, audio_man = media.resample_audio(
        segs, models["mic"], sc["audio_rate"], sc["duration_R"])
    media.write_wav("out_unified.wav", out_pcm, sc["audio_rate"])
    out_samples = [v[0] / 32767.0
                   for v in struct.iter_unpack("<h", out_pcm)]
    peaks = detect.independent_pulse_scan(out_samples, sc["audio_rate"])
    truth_R = sorted(sc["events_R"])
    used = set()
    verr = []
    for R in truth_R:
        cand = [k for k, y in enumerate(peaks)
                if abs(y - R) < 0.02 and k not in used]
        if cand:
            used.add(cand[0])
            verr.append(abs(peaks[cand[0]] - R) * 1000.0)
    spurious = len(peaks) - len(used)
    missed = len(truth_R) - len(used)
    print("独立复核：检出 %d、匹配 %d、误报 %d、漏检 %d、p95=%.3f ms" %
          (len(peaks), len(used), spurious, missed, p95(verr)))
    assert spurious == 0 and missed == 0
    assert p95(verr) <= P95_LIMIT_MS
    # 帧清单
    manifest = media.build_frame_manifest(
        sc["frames"], models["cam"], sc["cam_fps"], sc["duration_R"])
    ferr = [m["error_ms"] for m in manifest
            if m["calibrated"] and m["error_ms"] is not None]
    print("统一帧索引：%d 帧、校准 %d、未校准 %d、映射 p95=%.3f ms" %
          (len(manifest), len(ferr),
           len(manifest) - len(ferr), p95(ferr)))
    assert p95(ferr) <= P95_LIMIT_MS
    print("场景 1 通过 ✓\n")
    return models, report


def scenario_two():
    print("=" * 64)
    print("场景 2：移除整段同步标记 + 局部伪峰")
    print("=" * 64)
    # 移除 R=900..1140 的整段标记（含 9 个事件），注入 16 个伪峰
    sc = g.simulate(seed=4096, remove_window=(900.0, 1140.0), spurious=16)
    print("移除事件 %d 个、注入伪峰：相机 %d、录音 %d" %
          (len(sc["removed_R"]), len(sc["cam_spurious"]),
           len(sc["mic_spurious"])))
    flashes, tones, segs = detect_all(sc)
    models, report = pipeline.calibrate(flashes, tones)
    # 未校准区间应覆盖被移除窗口
    errs, unmapped = assess_mapping(sc, models)
    removed = set(sc["removed_R"])
    uncal_R = sorted(set(R for d, R in unmapped))
    covered_removed = removed.intersection(uncal_R)
    print("未映射检查点 %d 个，其中落在移除窗口 %d/%d 个" %
          (len(uncal_R), len(covered_removed), len(removed)))
    # 移除窗口内的检查点必须未校准
    missing_flag = sorted(removed - set(uncal_R))
    assert not missing_flag, "移除窗口内仍输出了映射: %s" % missing_flag[:5]
    # 正常段（移除窗口之外）的已映射点必须满足 5 ms 门槛
    normal_errs = []
    for d, dev in (("cam", sc["cam"]), ("mic", sc["mic"])):
        for ep, items in enumerate(checkpoint_epochs(dev, sc["checkpoints_R"])):
            for R, tick in items:
                if R in removed:
                    continue
                Rhat = map_tick(models[d][ep], tick) if ep in models[d] else None
                if Rhat is not None:
                    normal_errs.append(abs(Rhat - R) * 1000.0)
    print("正常段已映射点 %d、p95=%.3f ms、max=%.3f ms" %
          (len(normal_errs), p95(normal_errs), max(normal_errs)))
    assert p95(normal_errs) <= P95_LIMIT_MS
    # 伪峰不得污染拟合：各 epoch 拒绝/未配对统计
    print("配对数 %d；未配对相机标记 %d、录音标记 %d" %
          (report["pair_count"], len(report["unmatched_cam"]),
           len(report["unmatched_mic"])))
    # 独立复核：输出音轨在移除窗口无脉冲、其余正常
    out_pcm, _ = media.resample_audio(
        segs, models["mic"], sc["audio_rate"], sc["duration_R"])
    out_samples = [v[0] / 32767.0
                   for v in struct.iter_unpack("<h", out_pcm)]
    peaks = detect.independent_pulse_scan(out_samples, sc["audio_rate"])
    in_gap = [p for p in peaks if 900.0 <= p <= 1140.0]
    truth_kept = sorted(set(sc["events_R"]))
    used = set()
    for R in truth_kept:
        cand = [k for k, y in enumerate(peaks)
                if abs(y - R) < 0.02 and k not in used]
        if cand:
            used.add(cand[0])
    print("独立复核：移除窗口内脉冲 %d 个（应为 0），窗口外匹配 %d/%d" %
          (len(in_gap), len(used), len(truth_kept)))
    assert not in_gap, "移除窗口内出现了脉冲"
    assert len(used) == len(truth_kept)
    print("场景 2 通过 ✓\n")


if __name__ == "__main__":
    scenario_one()
    scenario_two()
    print("全部验收通过。")
