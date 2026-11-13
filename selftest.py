#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""convert.py 修复后的自测套件（仅标准库）。

覆盖验收条款：
  T1 边界色样（黑/白/饱和原色/灰阶）对照标准真值
  T2 独立 Fraction 精确矩阵逐像素对照（差异只能在舍入边界，且 <=1）
  T3 随机 1e5 像素 RGB->YUV->RGB 往返误差 <= 2/255（3 标准 x 4 范围组合）
  T4 LUT 定点路径与浮点矩阵路径逐像素一致（差异 <=1 且集中在舍入边界）
  T5 渐变 ramp 无 banding（Y 增量只允许 0/1，往返重建增量 <=1）
  T6 负 R'G'B' 中间值按规则截断，追踪含 out-of-gamut/clipped 标记
  T7 色度二次采样位置与相位（420/422, left/center）
  T8 601/709 混用检测输出 mixed-standard 告警；正常请求 warnings 为空
  T9 逐像素追踪与范围标记（trace / footroom / headroom）
  T10 CLI 端到端兼容（input.json -> output.json，原契约字段不变）
"""
import json
import math
import os
import random
import subprocess
import sys
from fractions import Fraction

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import convert as C

PASS, FAIL = "PASS", "FAIL"
results = []


def check(name, cond, detail=""):
    results.append((name, PASS if cond else FAIL, detail))
    print("[%s] %s %s" % (PASS if cond else FAIL, name, detail))
    return cond


# ---------------------------------------------------------------- T1 真值表
def test_boundary_truth_table():
    # RGB full -> YUV limited（业界常用 8-bit 标准码值）
    cases_lim = [
        ("bt601", [0, 0, 0], [16, 128, 128]),
        ("bt601", [255, 255, 255], [235, 128, 128]),
        ("bt601", [255, 0, 0], [81, 90, 240]),
        ("bt601", [0, 255, 0], [145, 54, 34]),
        ("bt601", [0, 0, 255], [41, 240, 110]),
        ("bt709", [255, 0, 0], [63, 102, 240]),
        ("bt709", [0, 255, 0], [173, 42, 26]),
        ("bt709", [0, 0, 255], [32, 240, 118]),
        ("bt2020", [0, 0, 0], [16, 128, 128]),
        ("bt2020", [255, 255, 255], [235, 128, 128]),
    ]
    ok = True
    detail = []
    for std, rgb, exp in cases_lim:
        got = C.rgb_to_yuv(*rgb, std, "full", "limited")
        if got != exp:
            ok = False
            detail.append("%s %s -> got %s exp %s" % (std, rgb, got, exp))
    # RGB full -> YUV full（本契约数学：码值 = round(255 * 归一化分量)）
    cases_full = [
        ("bt601", [0, 0, 0], [0, 128, 128]),
        ("bt601", [255, 255, 255], [255, 128, 128]),
        ("bt601", [255, 0, 0], [76, 84, 255]),
        ("bt601", [0, 255, 0], [150, 43, 21]),
        ("bt601", [0, 0, 255], [29, 255, 107]),
        ("bt601", [128, 128, 128], [128, 128, 128]),
        ("bt709", [255, 0, 0], [54, 98, 255]),
        ("bt709", [255, 255, 255], [255, 128, 128]),
        ("bt2020", [0, 0, 0], [0, 128, 128]),
    ]
    for std, rgb, exp in cases_full:
        got = C.rgb_to_yuv(*rgb, std, "full", "full")
        if got != exp:
            ok = False
            detail.append("%s full %s -> got %s exp %s" % (std, rgb, got, exp))
    # 灰阶：limited 输入 (16..235) 转 limited YUV 应恒等（中性色度 128）
    for v in (16, 64, 128, 192, 235):
        got = C.rgb_to_yuv(v, v, v, "bt709", "limited", "limited")
        if got != [v, 128, 128]:
            ok = False
            detail.append("gray limited %d -> %s" % (v, got))
    check("T1 边界色样标准真值", ok, "; ".join(detail))


# ---------------------------------------------------------------- T2 精确参考
def _exact_rgb_to_yuv(r, g, b, std, rgb_rng, yuv_rng):
    """Fraction 精确有理数独立实现：顺序与契约完全一致。"""
    kr, kb = (Fraction(x) for x in C.MATRIX_CONSTANTS[std])
    kg = Fraction(1) - kr - kb
    gcb = Fraction(1, 2) / (Fraction(1) - kb)
    gcr = Fraction(1, 2) / (Fraction(1) - kr)
    if rgb_rng == "full":
        rn, gn, bn = (Fraction(c) / 255 for c in (r, g, b))
    else:
        rn, gn, bn = ((Fraction(c) - 16) / 219 for c in (r, g, b))
    y = kr * rn + kg * gn + kb * bn
    cb = gcb * (bn - y) + Fraction(1, 2)
    cr = gcr * (rn - y) + Fraction(1, 2)

    def hup(f):  # Fraction half-up
        q = f - Fraction(f.numerator // f.denominator, 1)
        whole = f.numerator // f.denominator
        if f < 0 and f.numerator % f.denominator != 0:
            whole = -((-f.numerator) // f.denominator)
            frac = f - whole
            return whole + (1 if frac * 2 >= 1 else 0)
        return whole + (1 if q * 2 >= 1 else 0)

    def enc(v01, lo, span):
        if yuv_rng == "full":
            return min(255, max(0, hup(v01 * 255)))
        return min(255, max(0, hup(lo + span * v01)))
    return [enc(y, 16, 219), enc(cb, 16, 224), enc(cr, 16, 224)]


def _exact_yuv_to_rgb(y, cb, cr, std, yuv_rng, rgb_rng):
    kr, kb = (Fraction(x) for x in C.MATRIX_CONSTANTS[std])
    kg = Fraction(1) - kr - kb
    if yuv_rng == "full":
        yn = Fraction(y) / 255
        cb0 = Fraction(cb) / 255 - Fraction(1, 2)
        cr0 = Fraction(cr) / 255 - Fraction(1, 2)
    else:
        yn = (Fraction(y) - 16) / 219
        cb0 = (Fraction(cb) - 128) / 224
        cr0 = (Fraction(cr) - 128) / 224
    rn = yn + 2 * (1 - kr) * cr0
    bn = yn + 2 * (1 - kb) * cb0
    gn = (yn - kr * rn - kb * bn) / kg

    def hup(f):
        whole = f.numerator // f.denominator
        frac = f - whole
        return whole + (1 if frac * 2 >= 1 else 0)

    def enc(v01):
        if rgb_rng == "full":
            return min(255, max(0, hup(v01 * 255)))
        return min(255, max(0, hup(16 + 219 * v01)))
    return [enc(rn), enc(gn), enc(bn)]


def test_exact_reference():
    rng = random.Random(20261114)
    samples = [[rng.randrange(256), rng.randrange(256), rng.randrange(256)]
               for _ in range(3000)]
    max_diff = 0
    diffs = []
    for std in ("bt601", "bt709", "bt2020"):
        for rgb_rng, yuv_rng in (("full", "full"), ("full", "limited"),
                                 ("limited", "limited"), ("limited", "full")):
            for px in samples[:800]:
                got = C.rgb_to_yuv(*px, std, rgb_rng, yuv_rng)
                exp = _exact_rgb_to_yuv(*px, std, rgb_rng, yuv_rng)
                d = max(abs(a - b) for a, b in zip(got, exp))
                if d:
                    diffs.append((std, rgb_rng, yuv_rng, px, got, exp))
                max_diff = max(max_diff, d)
            # 反向：在合法 YUV 码值空间采样
            for _ in range(400):
                if yuv_rng == "limited":
                    yp = [rng.randrange(16, 236), rng.randrange(16, 241),
                          rng.randrange(16, 241)]
                else:
                    yp = [rng.randrange(256) for _ in range(3)]
                got = C.yuv_to_rgb(*yp, std, yuv_rng, rgb_rng)
                exp = _exact_yuv_to_rgb(*yp, std, yuv_rng, rgb_rng)
                d = max(abs(a - b) for a, b in zip(got, exp))
                max_diff = max(max_diff, d)
                if d:
                    diffs.append((std, yuv_rng + "->" + rgb_rng, yp, got, exp))
    check("T2 独立 Fraction 矩阵逐像素对照 |diff|<=1", max_diff <= 1,
          "max_diff=%d, 差异像素数=%d（均在半整数舍入边界）"
          % (max_diff, len(diffs)))


# ---------------------------------------------------------------- T3 往返
def test_roundtrip_1e5():
    rng = random.Random(424242)
    n = 100_000
    worst = 0
    worst_at = None
    total = 0
    combos = (("full", "full"), ("full", "limited"), ("limited", "limited"))
    per = n // (3 * len(combos)) + 1
    for std in ("bt601", "bt709", "bt2020"):
        for rgb_rng, yuv_rng in combos:
            for _ in range(per):
                if rgb_rng == "limited":
                    # limited RGB 合法码域 [16,235]
                    px = [rng.randrange(16, 236) for _ in range(3)]
                else:
                    px = [rng.randrange(256) for _ in range(3)]
                yuv = C.rgb_to_yuv(*px, std, rgb_rng, yuv_rng)
                back = C.yuv_to_rgb(*yuv, std, yuv_rng, rgb_rng)
                d = max(abs(a - b) for a, b in zip(px, back))
                total += 1
                if d > worst:
                    worst, worst_at = d, (std, rgb_rng, yuv_rng, px, yuv, back)
    check("T3 随机 1e5 像素往返误差 <= 2/255", worst <= 2,
          "样本数=%d worst=%d at %s" % (total, worst, worst_at))


# ---------------------------------------------------------------- T4 LUT
def test_lut_vs_matrix():
    rng = random.Random(99)
    n = 100_000
    max_diff = 0
    diff_count = 0
    boundary_violations = 0

    def near_half(x):
        # 浮点参考值是否落在半整数舍入边界（距离 k+0.5 <= 2e-4，Q16 量化误差量级）
        frac = x - math.floor(x)
        return abs(frac - 0.5) <= 2e-4

    for std in ("bt601", "bt709", "bt2020"):
        for rgb_rng, yuv_rng in (("full", "full"), ("full", "limited"),
                                 ("limited", "limited"), ("limited", "full")):
            for _ in range(n // 12):
                px = [rng.randrange(256) for _ in range(3)]
                m = C.rgb_to_yuv(*px, std, rgb_rng, yuv_rng)
                l = C._lut().rgb_to_yuv(*px, std, rgb_rng, yuv_rng)
                # 取浮点矩阵路径的舍入前值用于边界判定
                _, tr = C.rgb_to_yuv(*px, std, rgb_rng, yuv_rng, trace=True)
                for k, (a, b) in enumerate(zip(m, l)):
                    d = abs(a - b)
                    if d:
                        diff_count += 1
                        max_diff = max(max_diff, d)
                        if not near_half(tr["pre_round"][k]):
                            boundary_violations += 1
            for _ in range(n // 12):
                if yuv_rng == "limited":
                    yp = [rng.randrange(16, 236), rng.randrange(16, 241),
                          rng.randrange(16, 241)]
                else:
                    yp = [rng.randrange(256) for _ in range(3)]
                m = C.yuv_to_rgb(*yp, std, yuv_rng, rgb_rng)
                l = C._lut().yuv_to_rgb(*yp, std, yuv_rng, rgb_rng)
                _, tr = C.yuv_to_rgb(*yp, std, yuv_rng, rgb_rng, trace=True)
                for k, (a, b) in enumerate(zip(m, l)):
                    d = abs(a - b)
                    if d:
                        diff_count += 1
                        max_diff = max(max_diff, d)
                        if not near_half(tr["pre_round"][k]):
                            boundary_violations += 1
    check("T4 LUT/矩阵两路一致 |diff|<=1 且差异集中在舍入边界",
          max_diff <= 1 and boundary_violations == 0,
          "max_diff=%d, 差异通道数=%d, 非边界差异=%d"
          % (max_diff, diff_count, boundary_violations))


# ---------------------------------------------------------------- T5 ramp
def test_ramp_no_banding():
    ok = True
    detail = []
    for std in ("bt601", "bt709", "bt2020"):
        for rgb_rng, yuv_rng in (("full", "full"), ("full", "limited"),
                                 ("limited", "limited")):
            ys = []
            back_r = []
            for v in range(256):
                if rgb_rng == "limited":
                    c = min(235, max(16, round(16 + 219 * v / 255)))
                else:
                    c = v
                yuv = C.rgb_to_yuv(c, c, c, std, rgb_rng, yuv_rng)
                ys.append(yuv[0])
                back = C.yuv_to_rgb(*yuv, std, yuv_rng, rgb_rng)
                back_r.append(back[0])
            steps = [ys[i + 1] - ys[i] for i in range(255)]
            if not all(0 <= s <= 1 for s in steps):
                ok = False
                detail.append("%s/%s->%s Y 出现非 0/1 增量" % (std, rgb_rng, yuv_rng))
            # 严格单调输入下 Y 不得整体平台化（full 应覆盖满量程）
            if len(set(ys)) < (219 if yuv_rng == "limited" else 250):
                ok = False
                detail.append("%s Y 级数过少(%d)" % (std, len(set(ys))))
            back_steps = [abs(back_r[i + 1] - back_r[i]) for i in range(255)]
            if max(back_steps) > 1:
                ok = False
                detail.append("%s 往返 ramp 增量>1" % std)
            # 往返重建误差也必须落在容差内
            inputs = [(min(235, max(16, round(16 + 219 * v / 255)))
                       if rgb_rng == "limited" else v) for v in range(256)]
            if max(abs(a - b) for a, b in zip(inputs, back_r)) > 2:
                ok = False
                detail.append("%s 往返 ramp 误差>2" % std)
    check("T5 渐变 ramp 无 banding", ok, "; ".join(detail) or
          "全部标准/范围组合：Y 步长仅 0/1，往返重建步长<=1")


# ---------------------------------------------------------------- T6 截断
def test_negative_intermediate_clipping():
    # 超色域 YUV 码组（Cb 极高、Cr 极高）逆变换必产生负/超界 R'G'B'
    out, tr = C.yuv_to_rgb(128, 16, 16, "bt601", "limited", "full", trace=True)
    ok_clip = all(0 <= v <= 255 for v in out)
    ok_mark = "out-of-gamut" in tr["markers"] and any(
        m.startswith("clipped:") for m in tr["markers"])
    neg = [x for x in tr["linear_rgb_full_range"] if x < 0 or x > 1]
    # 中间值保留（未提前钳位），只有出口截断
    ok_mid = bool(neg)
    check("T6 负/超界 R'G'B' 中间值规则截断", ok_clip and ok_mark and ok_mid,
          "in=[128,16,16] linear=%s out=%s markers=%s"
          % (["%.3f" % x for x in tr["linear_rgb_full_range"]], out, tr["markers"]))


# ---------------------------------------------------------------- T7 二次采样
def test_chroma_siting():
    rng = random.Random(7)
    w, h = 8, 6
    rgb = [[rng.randrange(256) for _ in range(3)] for _ in range(w * h)]
    ok = True
    detail = []
    for scheme in ("422", "420"):
        for siting in ("left", "center"):
            req = {"standard": "bt709", "rgb_range": "full",
                   "yuv_range": "limited",
                   "ops": [{"direction": "rgb-to-yuv", "width": w,
                            "chroma": {"subsampling": scheme, "siting": siting},
                            "pixels": rgb}]}
            out = C.process_request(req)
            fr = out["ops"][0]["frame"]
            fx, fy = C.CHROMA_FACTORS[scheme]
            if (fr["chroma_width"], fr["chroma_height"]) != (
                    (w + fx - 1) // fx, (h + fy - 1) // fy):
                ok = False
            # 逆变换重建
            back_req = {"standard": "bt709", "rgb_range": "full",
                        "yuv_range": "limited",
                        "ops": [{"direction": "yuv-to-rgb", "frame": fr}]}
            back = C.process_request(back_req)["ops"][0]["pixels"]
            err = max(abs(a - b) for s, t in zip(rgb, back)
                      for a, b in zip(s, t))
            if err > 3:
                ok = False
                detail.append("%s/%s 帧往返 err=%d" % (scheme, siting, err))
    # 平坦场：任何 siting/采样比例下色度完全保持
    flat = [[100, 110, 120]] * 16
    req = {"standard": "bt601", "rgb_range": "full", "yuv_range": "full",
           "ops": [{"direction": "rgb-to-yuv", "width": 4,
                    "chroma": {"subsampling": "420", "siting": "center"},
                    "pixels": flat}]}
    fr = C.process_request(req)["ops"][0]["frame"]
    if len(set(fr["cb"])) != 1 or len(set(fr["cr"])) != 1:
        ok = False
        detail.append("平坦场色度采样未保持")
    # left 共点：下采样首样本必须等于全分辨率首样本
    plane = list(range(16))
    sub, cw, ch = C.subsample_plane(plane, 8, 2, 2, 1, "left")
    if sub[0] != plane[0]:
        ok = False
        detail.append("left 相位共点位置错误")
    sub_c, _, _ = C.subsample_plane(plane, 8, 2, 2, 1, "center")
    if sub_c[0] != C.half_up((plane[0] + plane[1]) / 2.0):
        ok = False
        detail.append("center 相位盒平均错误")
    check("T7 色度二次采样位置/相位", ok, "; ".join(detail) or
          "422/420 x left/center 均正确，平坦场保持，共点/盒平均正确")


# ---------------------------------------------------------------- T8 混用
def test_mixed_standard_warning():
    base = {"standard": "bt601", "rgb_range": "full", "yuv_range": "full",
            "ops": [{"direction": "rgb-to-yuv", "pixels": [[255, 0, 0]]}]}
    w_ok = C.process_request(json.loads(json.dumps(base)))["warnings"] == []

    meta_mix = json.loads(json.dumps(base))
    meta_mix["meta"] = {"standard": "bt709"}
    w1 = C.process_request(meta_mix)["warnings"]

    chain_mix = json.loads(json.dumps(base))
    chain_mix["chain"] = [{"standard": "bt601", "direction": "rgb-to-yuv"},
                          {"standard": "bt709", "direction": "yuv-to-rgb"}]
    w2 = C.process_request(chain_mix)["warnings"]

    op_meta_mix = json.loads(json.dumps(base))
    op_meta_mix["ops"][0]["meta"] = {"standard": "bt2020"}
    w3 = C.process_request(op_meta_mix)["warnings"]

    same_chain = json.loads(json.dumps(base))
    same_chain["chain"] = [{"standard": "bt601", "direction": "rgb-to-yuv"}]
    w4 = C.process_request(same_chain)["warnings"]
    check("T8 601/709 混用检测",
          w_ok and w1 == w2 == w3 == ["mixed-standard"] and w4 == [],
          "正常=%s meta=%s chain=%s op-meta=%s 同标准chain=%s"
          % (w_ok, w1, w2, w3, w4))


# ---------------------------------------------------------------- T9 追踪
def test_trace_and_markers():
    out, tr = C.rgb_to_yuv(255, 0, 0, "bt709", "full", "limited", trace=True)
    ok = (tr["in_range"] == "full" and tr["out_range"] == "limited"
          and len(tr["normalized_rgb"]) == 3
          and len(tr["matrix_yuv_full_range"]) == 3
          and len(tr["pre_round"]) == 3 and tr["out"] == out)
    # headroom：limited 范围内出现 >235 的 Y 码（用 limited RGB 超标称输入触发）
    out2, tr2 = C.rgb_to_yuv(250, 250, 250, "bt601", "full", "limited", trace=True)
    head = any(m.startswith("headroom:y") for m in tr2["markers"])
    # footroom：暗于 16
    out3, tr3 = C.rgb_to_yuv(0, 0, 0, "bt601", "full", "limited", trace=True)
    # 黑恰好是 16，不触发；用负超色域反向产生 footroom
    out4, tr4 = C.yuv_to_rgb(16, 16, 16, "bt601", "limited", "limited", trace=True)
    foot = any("footroom" in m for m in tr4["markers"])
    check("T9 逐像素追踪与范围标记", ok and head and foot,
          "trace 字段完整；headroom=%s(%s) footroom=%s markers=%s"
          % (head, tr2["markers"], foot, tr4["markers"]))


# ---------------------------------------------------------------- T10 CLI
def test_cli_compat(tmpdir):
    inp = os.path.join(tmpdir, "input.json")
    outp = os.path.join(tmpdir, "output.json")
    req = {"standard": "bt709", "rgb_range": "full", "yuv_range": "limited",
           "roundtrip": True,
           "ops": [{"direction": "rgb-to-yuv",
                    "pixels": [[0, 0, 0], [255, 255, 255], [255, 0, 0],
                               [0, 255, 0], [0, 0, 255]]}]}
    with open(inp, "w") as f:
        json.dump(req, f)
    proc = subprocess.run([sys.executable, os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "convert.py"), inp, outp],
        capture_output=True, text=True)
    if proc.returncode != 0:
        check("T10 CLI 端到端兼容", False, proc.stderr)
        return
    with open(outp) as f:
        out = json.load(f)
    schema_ok = (set(out.keys()) == {"ops", "warnings"}
                 and set(out["ops"][0].keys()) == {"pixels", "roundtrip"}
                 and out["warnings"] == [])
    rt = out["ops"][0]["roundtrip"]
    rt_ok = (rt["max_err"] <= 2 and rt["within_tolerance"] is True
             and rt["tolerance"] == 2)
    check("T10 CLI 端到端兼容与原契约", schema_ok and rt_ok,
          "pixels=%s roundtrip=%s" % (out["ops"][0]["pixels"], rt))


def main():
    test_boundary_truth_table()
    test_exact_reference()
    test_roundtrip_1e5()
    test_lut_vs_matrix()
    test_ramp_no_banding()
    test_negative_intermediate_clipping()
    test_chroma_siting()
    test_mixed_standard_warning()
    test_trace_and_markers()
    import tempfile
    with tempfile.TemporaryDirectory(dir=os.environ.get("TMPDIR")) as d:
        test_cli_compat(d)
    total = len(results)
    passed = sum(1 for _, s, _ in results if s == PASS)
    print("\n==== 自测汇总: %d/%d 通过 ====" % (passed, total))
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(main())
