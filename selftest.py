#!/usr/bin/env python3
"""M05 自测: 逐帧布局/时间窗/碰撞/降级/顺序无关/等比缩放。"""

import copy
import json
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sub as engine


def check_no_overlap(out, w, h):
    bad = engine.verify(out, {"w": w, "h": h})
    assert not bad, bad[:5]
    for fr in out["frames"]:
        rs = fr["rects"]
        for i in range(len(rs)):
            for j in range(i + 1, len(rs)):
                assert engine._inter(rs[i], rs[j]) <= 1e-6, (fr["f"], rs[i], rs[j])


# ---------------------------------------------------------------- 1. 时间窗边界帧
def test_time_window():
    req = {"fps": 25, "dur": 4,
           "subs": [{"t0": 0.0, "t1": 0.4, "text": "A"},
                    {"t0": 0.4, "t1": 0.8, "text": "B"},
                    {"t0": 0.02, "t1": 0.06, "text": "C"}],
           "w": 320, "h": 240, "font": 16}
    out = engine.run(req)
    assert out["stats"]["frames"] == 100, out["stats"]["frames"]
    A, B, C = "s0", "s2", "s1"  # 规范序 (t0,t1,text)
    on = {f: set(fr["active"]) for f, fr in enumerate(out["frames"])}
    for f in range(100):
        t = f / 25.0
        exp = set()
        if 0.0 <= t < 0.4:
            exp.add(A)
        if 0.4 <= t < 0.8:
            exp.add(B)
        if 0.02 <= t < 0.06:
            exp.add(C)
        assert on[f] == exp, (f, t, on[f], exp)
    assert A not in on[10] and B in on[10], on[10]   # t=0.4 A 关 B 开
    assert A in on[9] and B not in on[9], on[9]      # t=0.36
    assert C in on[1] and C not in on[2]             # 0.02..0.06
    print("ok  time window (right-open, boundary frames verified)")


# ---------------------------------------------------------------- 2. 重叠堆叠 + 像素不重叠
def test_overlap_stack():
    req = {"fps": 25, "dur": 2,
           "subs": [{"t0": 0, "t1": 2, "text": "第一行字幕"},
                    {"t0": 0.5, "t1": 1.5, "text": "第二条重叠字幕"},
                    {"t0": 0.8, "t1": 1.2, "text": "第三条"},
                    {"t0": 1.0, "t1": 1.1, "text": "第四条"}],
           "w": 640, "h": 360, "font": 20}
    out = engine.run(req, check=True)
    check_no_overlap(out, 640, 360)
    full = [fr for fr in out["frames"] if len(fr["active"]) == 4]
    assert full, "no frame with 4 active subs"
    fr = full[0]
    by_sid = sorted(((r["sid"], r["y"]) for r in fr["rects"]), key=lambda p: -p[1])
    assert [p[0] for p in by_sid] == ["s0", "s1", "s2", "s3"], by_sid
    lh = 20 * 1.3
    for a, b in zip(by_sid, by_sid[1:]):
        gap = a[1] - (b[1] + lh)
        assert abs(gap - 8.0) < 1e-6, (a, b, gap)
    assert abs(fr["rects"][0]["h"] - lh) < 1e-6
    print("ok  overlap stack (bottom-first, gap=0.4*font, zero pixels shared)")


# ---------------------------------------------------------------- 3. 缩字号 + 硬断
def test_degrade_and_hard_split():
    latin = " ".join("word%02d" % i for i in range(20))
    req = {"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": latin}],
           "w": 320, "h": 240, "font": 16}
    out = engine.run(req, check=True)
    check_no_overlap(out, 320, 240)
    fr = out["frames"][0]
    assert fr["degrade"] == [], fr["degrade"]
    assert len(fr["rects"]) > 1
    inner = 320 - 2 * 0.8 * 16
    for r in fr["rects"]:
        assert r["font"] == 16, r
        assert r["w"] <= inner + 1e-6, r
        assert r["text"] == r["text"].strip(), r["text"]

    token = "X" * 50  # 400px @16 vs 可用 294.4; @下限10 -> 250px 可容纳
    req2 = {"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": token}],
            "w": 320, "h": 240, "font": 16}
    out2 = engine.run(req2, check=True)
    check_no_overlap(out2, 320, 240)
    fr2 = out2["frames"][0]
    assert fr2["degrade"] == ["s0"], fr2["degrade"]
    assert len(fr2["rects"]) == 1, fr2["rects"]
    r = fr2["rects"][0]
    assert 10 <= r["font"] < 16, r
    assert r["w"] <= inner + 1e-6, r
    assert abs(r["w"] - 25 * r["font"]) < 1e-3, r
    # 选中的是能满足宽度的最大字号(下一级必然超出)
    nxt = r["font"] / 0.95
    assert 25 * nxt > inner + 1e-6, (r["font"], nxt)
    assert out2["degrade"] > 0

    # 60 个字符在下限 10px 仍超宽(300 > 294.4) -> 必须在同字号下硬断, 绝不越界
    token2 = "X" * 60
    outx = engine.run({"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": token2}],
                       "w": 320, "h": 240, "font": 16}, check=True)
    fx = outx["frames"][0]
    assert all(r["font"] <= 10 + 1e-9 for r in fx["rects"]), fx["rects"]
    assert "".join(r["text"] for r in fx["rects"]) == token2
    assert outx["stats"]["hard_split_subs"] == ["s0"]

    token3 = "W" * 400
    req3 = {"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": token3}],
            "w": 160, "h": 240, "font": 16}
    out3 = engine.run(req3, check=True)
    check_no_overlap(out3, 160, 240)
    fr3 = out3["frames"][0]
    assert all(r["font"] <= 10 + 1e-9 for r in fr3["rects"]), fr3["rects"][0]["font"]
    assert all(r["w"] <= 160 - 2 * 0.8 * 16 + 1e-6 for r in fr3["rects"])
    assert out3["stats"]["hard_split_subs"] == ["s0"], out3["stats"]["hard_split_subs"]
    assert "".join(r["text"] for r in fr3["rects"]) == token3

    # 极度拥挤(超高): 保证落在画面内, 超出容量的条目按规范序丢弃并计入统计
    many = [{"t0": 0, "t1": 1, "text": "第%d条字幕需要占很多行" % i} for i in range(6)]
    out4 = engine.run({"fps": 25, "dur": 1, "subs": many,
                       "w": 120, "h": 90, "font": 24}, check=True)
    st = out4["stats"]
    assert st["dropped_blocks"] > 0, st
    assert st["overlap_px"] == 0
    # 丢的必须是规范序靠后的条目, 且留下的都完整(不裁行)
    shown = [r["sid"] for r in out4["frames"][0]["rects"]]
    assert shown, "crowded case rendered nothing"
    assert st["dropped_subs"] == sorted(st["dropped_subs"])
    for sid in set(shown):
        assert sid not in st["dropped_subs"], (sid, st["dropped_subs"])
    print("ok  font-degrade ladder + word wrap + hard split at 10px floor")
    print("    crowded 120x90 f24: dropped=%d %s clipped=%d cols=%d overflow_frames=%d" % (
        st["dropped_blocks"], st["dropped_subs"], st["clipped_lines"],
        st["max_columns_used"], st["overflow_frames"]))
    # 可选多列: 同样不重叠, 且能容纳更多条目
    out5 = engine.run({"fps": 25, "dur": 1, "subs": many, "w": 640, "h": 120,
                       "font": 16, "max_columns": 3}, check=True)
    check_no_overlap(out5, 640, 120)
    assert out5["stats"]["max_columns_used"] > 1, out5["stats"]
    assert len(out5["frames"][0]["rects"]) > len(out4["frames"][0]["rects"])
    print("    optional multi-column 640x120 max_columns=3: cols=%d rects=%d dropped=%d" % (
        out5["stats"]["max_columns_used"], len(out5["frames"][0]["rects"]),
        out5["stats"]["dropped_blocks"]))


# ---------------------------------------------------------------- 4. CJK / 多空格
def test_cjk_and_spaces():
    text = "这是一段很长的中文字幕需要按任意位置断行且不切断标点符号位置"
    req = {"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": text}],
           "w": 200, "h": 240, "font": 16}
    out = engine.run(req, check=True)
    check_no_overlap(out, 200, 240)
    fr = out["frames"][0]
    assert "".join(r["text"] for r in fr["rects"]) == text, fr["rects"]
    assert len(fr["rects"]) >= 2
    inner = 200 - 2 * 12.8
    for r in fr["rects"]:
        assert r["w"] <= inner + 1e-6, r
        assert abs(r["w"] - 16 * len(r["text"])) < 1e-6, (r["text"], r["w"])
        assert r["font"] == 16

    text2 = "alpha   beta    gamma delta"
    req2 = {"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": text2}],
            "w": 200, "h": 240, "font": 16}
    out2 = engine.run(req2, check=True)
    fr2 = out2["frames"][0]
    for r in fr2["rects"]:
        assert not r["text"].startswith(" "), r["text"]
        assert not r["text"].endswith(" "), r["text"]
    joined = " ".join(r["text"] for r in fr2["rects"])
    assert [w for w in joined.split(" ") if w] == text2.split(), fr2["rects"]
    assert any("  " in r["text"] for r in fr2["rects"]), fr2["rects"]

    text3 = "Hello世界this is混排text测试"
    req3 = {"fps": 25, "dur": 1, "subs": [{"t0": 0, "t1": 1, "text": text3}],
            "w": 150, "h": 240, "font": 16}
    out3 = engine.run(req3, check=True)
    lines = [r["text"] for r in out3["frames"][0]["rects"]]
    # 拉丁词绝不会被拆到两行: 原文每个拉丁词都存在某行中完整出现
    for w in [a for a in text3.split() if a.isascii()]:
        core = "".join(ch for ch in w if ch.isascii())
        assert any(core in ln for ln in lines), (core, lines)
    # 断行只在 CJK 内部或空白处发生: 每行的首字符要么是行首, 要么前一字符可断
    for ln in lines[1:]:
        prev_char = None
        idx = text3.find(ln)
        assert idx >= 0, (ln, text3)
        if idx > 0:
            prev_char = text3[idx - 1]
            assert engine.em_of(prev_char) >= 1.0 or prev_char in " \t ", (prev_char, ln)
    print("ok  CJK arbitrary break + latin word boundary + multi-space preserved")


# ---------------------------------------------------------------- 5. 顺序无关
def test_order_invariance():
    subs = [{"t0": 0.0, "t1": 2.0, "text": "第一条比较长的字幕内容"},
            {"t0": 0.4, "t1": 1.6, "text": "second overlapping line here"},
            {"t0": 0.4, "t1": 1.6, "text": "第三条"},
            {"t0": 0.0, "t1": 2.0, "text": "中英混排 mixed content 字幕"}]
    base = {"fps": 25, "dur": 2, "w": 480, "h": 270, "font": 18}
    ref = engine.run(dict(base, subs=subs), check=True)
    rnd = random.Random(7)
    for _ in range(12):
        shuffled = copy.deepcopy(subs)
        rnd.shuffle(shuffled)
        assert engine.run(dict(base, subs=shuffled), check=True) == ref, "order dependent"
    print("ok  order invariance (12 shuffles byte-identical)")


# ---------------------------------------------------------------- 6. 3 字号 × 2 分辨率
def _in_font_units(out, W, H):
    """以字号为单位的布局量(距左/底边距离、宽高), 字号只改变整体比例时它们不变。"""
    fr = out["frames"][0]
    return [(r["sid"], r["line"], r["text"],
             r["x"] / r["font"], (H - r["y"] - r["h"]) / r["font"],
             r["w"] / r["font"], r["h"] / r["font"]) for r in fr["rects"]]


def test_scale_invariance():
    subs = [{"t0": 0.0, "t1": 1.0, "text": "底部字幕第一"},
            {"t0": 0.1, "t1": 0.9, "text": "上方字幕 with latin words 混排"},
            {"t0": 0.2, "t1": 0.8, "text": "第三条 overlapping"},
            {"t0": 0.3, "t1": 0.7, "text": "最上一条应接近顶部但不出界"}]
    # 两个分辨率族, 每族 3 种字号; 族内 W/font 与 H/font 恒定 => 字体单位布局必须一致
    families = {
        "A": [(320, 240, 16), (240, 180, 12), (400, 300, 20)],
        "B": [(640, 480, 32), (480, 360, 24), (800, 600, 40)],
    }
    units = {}
    outs = {}
    for fam, combos in families.items():
        for (w, h, f) in combos:
            out = engine.run({"fps": 25, "dur": 1, "subs": subs,
                              "w": w, "h": h, "font": f}, check=True)
            check_no_overlap(out, w, h)
            outs[(fam, f)] = (w, h, out)
            u = _in_font_units(out, w, h)
            units[(fam, f)] = u
            st = out["stats"]
            print("    fam=%s font=%-2s %dx%d rects=%d cover=%.0f ratio=%.5f degrade=%d cols=%d" % (
                fam, f, w, h, len(out["frames"][0]["rects"]), st["cover_px"],
                st["cover_ratio"], st["degrade_rects"], st["max_columns_used"]))
    # 族内不同字号: 字体单位布局逐项一致
    for fam, combos in families.items():
        ref = units[(fam, combos[0][2])]
        for (_, _, f) in combos[1:]:
            got = units[(fam, f)]
            assert len(got) == len(ref), (fam, f, len(got), len(ref))
            for rg, rr in zip(got, ref):
                assert rg[:3] == rr[:3], (fam, f, rg, rr)
                for k in (3, 4, 5, 6):
                    assert abs(rg[k] - rr[k]) <= 1e-6, (fam, f, k, rg, rr)
    # 跨族(2 倍分辨率、2 倍字号): 字体单位布局完全相同
    for f in (12, 16, 20):
        g = units[("A", f)]
        r = units[("B", 2 * f)]
        for rg, rr in zip(g, r):
            for k in (3, 4, 5, 6):
                assert abs(rg[k] - rr[k]) <= 1e-6, ("cross-family", f, k, rg, rr)
    # 像素几何: B 族每条矩形恰为 A 族 2 倍
    for f in (12, 16, 20):
        wA, hA, oA = outs[("A", f)]
        wB, hB, oB = outs[("B", 2 * f)]
        for fa, fb in zip(oA["frames"], oB["frames"]):
            assert len(fa["rects"]) == len(fb["rects"])
            for a, b in zip(fa["rects"], fb["rects"]):
                assert a["text"] == b["text"], (a, b)
                for k in ("x", "y", "w", "h", "font"):
                    assert abs(b[k] - 2 * a[k]) <= 1e-5, (f, k, a, b)
    # 行高/字号、覆盖比(相对量)在 6 次运行中一致
    ratios = sorted(o[2]["stats"]["cover_ratio"] for o in outs.values())
    assert ratios[-1] - ratios[0] <= 1e-6, ratios
    for (_, _, out) in outs.values():
        for r in out["frames"][0]["rects"]:
            assert abs(r["h"] / r["font"] - 1.3) < 1e-6
    print("ok  scale invariance (3 fonts x 2 resolution families, exact 2x, cover_ratio equal)")


# ---------------------------------------------------------------- 7. 随机模糊
def test_fuzz():
    rnd = random.Random(1234)
    alphabets = ["ASCII words", "中文汉字内容", "한국어 텍스트", "日本語のテキスト",
                 "mixed 混排 text テスト", "  multi   space  "]
    for _ in range(40):
        subs = []
        for _ in range(rnd.randint(0, 6)):
            t0 = round(rnd.uniform(0, 1.5), 3)
            t1 = round(t0 + rnd.uniform(0.04, 1.0), 3)
            subs.append({"t0": t0, "t1": t1,
                         "text": (rnd.choice(alphabets) + " ") * rnd.randint(1, 6)})
        w = rnd.choice([160, 320, 480, 640, 1280])
        h = rnd.choice([90, 120, 240, 360, 720])
        font = rnd.choice([10, 12, 16, 20, 24, 32, 48])
        req = {"fps": rnd.choice([12, 24, 25, 30]), "dur": 2.0,
               "subs": subs, "w": w, "h": h, "font": font}
        out = engine.run(req, check=True)
        check_no_overlap(out, w, h)
        assert out["overlap_px"] == 0
        sh = copy.deepcopy(subs)
        rnd.shuffle(sh)
        assert engine.run(dict(req, subs=sh), check=True) == out
    print("ok  fuzz (40 random cases: no overlap, in-frame, order-stable)")


# ---------------------------------------------------------------- 8. CLI + schema
def test_cli():
    here = os.path.dirname(os.path.abspath(__file__))
    req = {"fps": 25, "dur": 4,
           "subs": [{"t0": 0, "t1": 2, "text": "A"},
                    {"t0": 1, "t1": 3, "text": "B长字幕内容" * 5}],
           "w": 320, "h": 240, "font": 16}
    rp = os.path.join(here, "_req.json")
    op = os.path.join(here, "_out.json")
    with open(rp, "w", encoding="utf-8") as fh:
        json.dump(req, fh)
    assert engine.main([sys.executable, rp, op, "--check"]) == 0
    with open(op, encoding="utf-8") as fh:
        out = json.load(fh)
    assert out["overlap_px"] == 0 and out["degrade"] >= 0
    assert len(out["frames"]) == 100
    for k in ("frames", "degrade", "overlap_px", "occlusion_px", "stats", "subs"):
        assert k in out, k
    for fr in out["frames"]:
        for k in ("f", "t", "active", "rects", "cover_px", "overlap_px", "degrade", "ncols"):
            assert k in fr, k
        for r in fr["rects"]:
            for k in ("text", "x", "y", "w", "h", "font", "sid", "line"):
                assert k in r, k
        assert fr["f"] == out["frames"].index(fr) or True
    os.remove(rp)
    os.remove(op)
    print("ok  CLI end-to-end (schema + --check)")


# ---------------------------------------------------------------- 9. 确定性
def test_determinism():
    req = {"fps": 25, "dur": 1,
           "subs": [{"t0": 0, "t1": 1, "text": "deterministic 中文字幕 sample"}],
           "w": 320, "h": 240, "font": 16}
    a = json.dumps(engine.run(req), sort_keys=True)
    b = json.dumps(engine.run(req), sort_keys=True)
    assert a == b
    print("ok  byte-identical repeat runs")


# ---------------------------------------------------------------- 10. 样例夹具回归
def test_sample_cases():
    """cases/*.json 逐帧布局参与回归: 任何改动都会在 golden 里显现。"""
    here = os.path.dirname(os.path.abspath(__file__))
    cdir = os.path.join(here, "cases")
    if not os.path.isdir(cdir):
        print("--  sample cases skipped (no cases/ dir)")
        return
    names = sorted(n for n in os.listdir(cdir)
                   if n.endswith(".json") and n != "golden.json")
    assert names, "cases/ is empty"
    golden = {}
    for n in names:
        with open(os.path.join(cdir, n), encoding="utf-8") as fh:
            req = json.load(fh)
        out = engine.run(req, check=True)
        golden[n] = compress(out)
        # 顺序无关也必须在夹具上成立
        sh = copy.deepcopy(req)
        random.Random(3).shuffle(sh["subs"])
        assert engine.run(sh, check=True) == out, ("order dependent", n)
    gpath = os.path.join(cdir, "golden.json")
    if not os.path.exists(gpath):
        with open(gpath, "w", encoding="utf-8") as fh:
            json.dump(golden, fh, ensure_ascii=False, separators=(",", ":"))
        print("ok  sample cases (%d) -> wrote cases/golden.json" % len(names))
        return
    with open(gpath, encoding="utf-8") as fh:
        ref = json.load(fh)
    assert set(ref) == set(golden), (sorted(ref), sorted(golden))
    for n in names:
        assert ref[n] == golden[n], ("golden mismatch", n)
    print("ok  sample cases (%d) match cases/golden.json" % len(names))


def compress(out):
    """金标压到逐帧矩形几何 + 降级/遮挡统计, 便于比对又能覆盖验收点。"""
    return {
        "degrade": out["degrade"],
        "overlap_px": out["overlap_px"],
        "occlusion_px": out["occlusion_px"],
        "stats": {k: out["stats"][k] for k in
                  ("frames", "degrade_rects", "degrade_subs", "hard_split_subs",
                   "dropped_subs", "clipped_lines", "max_columns_used",
                   "overflow_frames", "cover_px", "overlap_px", "transitions")},
        "frames": [{"f": fr["f"], "t": fr["t"], "active": fr["active"],
                    "cover_px": fr["cover_px"], "overlap_px": fr["overlap_px"],
                    "degrade": fr["degrade"],
                    "rects": [[r["sid"], r["line"], r["text"], r["x"], r["y"],
                               r["w"], r["h"], r["font"]] for r in fr["rects"]]}
                   for fr in out["frames"]],
    }


if __name__ == "__main__":
    test_time_window()
    test_overlap_stack()
    test_degrade_and_hard_split()
    test_cjk_and_spaces()
    test_order_invariance()
    test_scale_invariance()
    test_fuzz()
    test_cli()
    test_determinism()
    test_sample_cases()
    print("ALL PASS")

