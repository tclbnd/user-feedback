#!/usr/bin/env python3
"""字幕逐帧时间窗与碰撞布局。

用法:  python3 sub.py req.json output.json [--check]

请求:
  {"fps":25,"dur":4,"subs":[{"t0":0,"t1":2,"text":"A"}],"w":320,"h":240,"font":16}

约定(均与 font/分辨率成正比, 因此等比缩放后相对布局不变):
  * 帧 f 的时刻 t = f/fps; 激活条件 t0 <= t < t1 (右开区间, 用有理数精确比较)
  * 度量: 窄字符 0.5em, 宽/全角(W,F) 1.0em, 组合/格式字符 0em; 行高 = font*1.3
  * 断行: 空白为断点(拉丁词边界), 宽字符(CJK 等)任意处可断; 行首尾空白裁掉, 行内
          多空格原样保留; 单词语超宽 -> 先缩字号, 到下限仍超宽才按字符硬断
  * 缩字号: font*0.95^k 逐级下降, 直到所有行 <= 可用宽且块高 <= 可用高;
          下限 = max(10, 0.6*font); 到下限仍超高记 overflow
  * 堆叠: 规范序 (t0,t1,text) 升序; 第一条贴底, 其余逐条向上, 条间留 0.4*font 间隙;
          默认单列(纯纵向堆叠); max_columns>1 时可开新列, 放不下的条目按规范序丢弃并统计
"""

import json
import math
import sys
import unicodedata
from fractions import Fraction

EPS = 1e-9
DIGITS = 6

NARROW_EM = 0.5
WIDE_EM = 1.0

DEFAULT_CFG = {
    "fps": 25,
    "w": 320,
    "h": 240,
    "font": 16.0,
    "line_spacing": 1.3,    # 行高 = font * line_spacing
    "side_margin": 0.8,     # 左右安全边 = font * side_margin
    "bottom_margin": 0.6,   # 底部安全边
    "top_margin": 0.6,      # 顶部安全边
    "vertical_gap": 0.4,    # 条间垂直间隙
    "column_gap": 0.5,      # 列间水平间隙
    "font_step": 0.95,      # 缩字号倍率
    "min_font": None,       # 绝对下限(显式指定则优先)
    "min_font_ratio": 0.6,  # 相对下限 = font * ratio
    "min_font_abs": 10.0,   # 可读下限
    "min_col_em": 8.0,      # 多列时每列最窄宽度 = min_col_em * font
    "max_columns": 1,       # 默认只做纵向堆叠; >1 时允许开新列(可选)
}
# ---------------------------------------------------------------- 字符度量
def em_of(ch):
    """单字符宽度(em)。确定性度量表, 不依赖系统字体。"""
    if unicodedata.combining(ch):
        return 0.0
    if unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0.0
    if ch.isspace():
        return 2.0 if ch == "\t" else NARROW_EM
    if unicodedata.east_asian_width(ch) in ("W", "F"):
        return WIDE_EM
    return NARROW_EM


def measure(s, font):
    return math.fsum(em_of(ch) for ch in s) * font


# ---------------------------------------------------------------- 断行/断词
# 禁则处理: 行首不允许出现的字符(收尾标点、闭合括号等)会与前一字符粘成一个单元,
# 行尾不允许出现的字符(起始括号、开引号等)会与后一字符粘成一个单元。
_NO_LINE_START = set("，。、；：！？）］｝〉》」』】”’·…ー々〜％‰℃、,.;:!?)]}»" )
_NO_LINE_END = set("（［｛〈《「『【“‘([{«")


def build_cells(text):
    """切成 "空白前缀 + 不可分单元": 空白是断点, 附着到后继单元; 末尾空白丢弃。

    拉丁单词本身是不可分单元(词边界即断点), CJK 逐字成单元(任意处可断);
    再按禁则把不该拆开的相邻单元粘合。
    """
    atoms = []
    lead = ""
    for ch in text:
        e = em_of(ch)
        if e == 0.0:
            if atoms:
                atoms[-1][1] += ch
            else:
                lead += ch
            continue
        kind = "ws" if ch.isspace() else ("wide" if e >= WIDE_EM else "word")
        if atoms and atoms[-1][0] == kind and kind in ("ws", "word"):
            atoms[-1][1] += ch
        else:
            atoms.append([kind, ch])
    if lead:
        if atoms:
            atoms[0][1] = lead + atoms[0][1]
        else:
            atoms.append(["word", lead])

    merged = []
    for kind, s in atoms:
        if merged and kind != "ws":
            prev = merged[-1]
            glue = (prev[0] != "ws" and (s[0] in _NO_LINE_START or prev[1][-1] in _NO_LINE_END))
            if glue:
                prev[1] += s
                continue
        merged.append([kind, s])

    cells = []
    pend, pend_w = "", 0.0
    for kind, s in merged:
        w = measure(s, 1.0)
        if kind == "ws":
            pend += s
            pend_w += w
        else:
            cells.append({"lead": pend, "lead_w": pend_w, "text": s, "w": w})
            pend, pend_w = "", 0.0
    return cells


def _hard_split(s, font, max_w):
    """超宽单元按字符硬断; 单字符仍超宽时独占一行(无法再切)。"""
    out, buf, bw = [], "", 0.0
    for ch in s:
        cw = measure(ch, font)
        if buf and bw + cw > max_w + EPS:
            out.append(buf)
            buf, bw = "", 0.0
        if not buf and cw > max_w + EPS:
            out.append(ch)
            continue
        buf += ch
        bw += cw
    if buf:
        out.append(buf)
    return out


def wrap(text, font, max_w, hard_split=False):
    """度量驱动贪心断行, 返回 (lines, ok)。ok=False 表示有行仍超宽(需缩字号)。"""
    cells = build_cells(text)
    lines = []
    ok = True
    i, n = 0, len(cells)
    while i < n:
        cell = cells[i]
        w0 = cell["w"] * font
        if w0 > max_w + EPS:
            if not hard_split:
                ok = False
                lines.append(cell["text"])
                i += 1
                continue
            lines.extend(_hard_split(cell["text"], font, max_w))
            i += 1
            continue
        cur_text = cell["text"]
        cur_w = w0
        j = i + 1
        while j < n:
            nxt = cells[j]
            add = (nxt["lead_w"] + nxt["w"]) * font
            if cur_w + add > max_w + EPS:
                break
            cur_text += nxt["lead"] + nxt["text"]
            cur_w += add
            j += 1
        lines.append(cur_text)
        i = j
    return lines, ok


# ---------------------------------------------------------------- 字号与单条布局
def min_font_floor(cfg):
    if cfg.get("min_font"):
        return float(cfg["min_font"])
    return max(float(cfg["min_font_abs"]), float(cfg["font"]) * float(cfg["min_font_ratio"]))


def font_series(cfg):
    """从基准字号乘性下降的字号序列, 末项为下限。"""
    base = float(cfg["font"])
    floor = min(min_font_floor(cfg), base)
    step = float(cfg["font_step"])
    series = [base]
    f = base
    while f * step > floor + EPS:
        f *= step
        series.append(f)
    if abs(series[-1] - floor) > EPS:
        series.append(floor)
    out = []
    for f in series:
        if not out or abs(out[-1] - f) > EPS:
            out.append(f)
    return out


def layout_block(text, cfg, max_w, max_h):
    """单条字幕在给定列宽下布局: 取能容纳的最大字号, 度量驱动断行。

    到下限仍超高时, 保留能放下的行并记录 dropped(不越界, 可统计降级)。
    """
    text = text if isinstance(text, str) else str(text)
    if not text.strip():
        return None
    lsp = float(cfg["line_spacing"])
    chosen = None
    for f in font_series(cfg):
        lines, ok = wrap(text, f, max_w)
        if not ok or not lines:
            continue
        lh = f * lsp
        if len(lines) * lh > max_h + EPS:
            continue
        chosen = (f, lines, lh, False)
        break
    dropped = 0
    if chosen is None:
        f = min(min_font_floor(cfg), float(cfg["font"]))
        lines, _ = wrap(text, f, max_w, hard_split=True)
        if not lines:
            return None
        lh = f * lsp
        if lh * len(lines) > max_h + EPS:
            nfit = int(max_h // lh + EPS) if lh > EPS else 0
            nfit = max(nfit, 1) if lines else 0
            dropped = max(len(lines) - nfit, 0)
            lines = lines[:nfit]
            if lh * len(lines) > max_h + EPS:
                lh = max_h if lines else lh  # 单行仍超高: 压到可用高
        chosen = (f, lines, lh, True)
    f, lines, lh, hard = chosen
    widths = [measure(t, f) for t in lines]
    return {
        "font": f,
        "lines": [{"text": t, "w": w} for t, w in zip(lines, widths)],
        "lh": lh,
        "h": lh * len(lines),
        "w": max(widths) if widths else 0.0,
        "hard": hard,
        "dropped": dropped,
        "overflow": dropped > 0,
        "shrunk": f < float(cfg["font"]) - EPS,
    }


# ---------------------------------------------------------------- 列装箱 + 堆叠
def pack_columns(blocks, ncols, avail_h, gap):
    """规范序逐条贴底堆叠, 一列放不下开下一列。

    列用尽仍放不下的块被丢弃(不越界): 规范序优先级高者先占位, 记 dropped。
    返回 (cols, dropped)。
    """
    cols = [[] for _ in range(ncols)]
    col = 0
    used = 0.0
    dropped = []
    for b in blocks:
        if col >= ncols:  # 列已用尽: 规范序靠后的块一律丢弃, 不再尝试摆放
            dropped.append(b)
            continue
        need = b["h"] if used <= EPS else b["h"] + gap
        if used + need > avail_h + EPS and used > EPS:
            col += 1
            if col >= ncols:
                dropped.append(b)
                continue
            used = 0.0
            need = b["h"]
        if need > avail_h + EPS:
            dropped.append(b)
            continue
        cols[col].append(b)
        used += need
    return cols, dropped


def q(v):
    """输出量化: 统一保留 DIGITS 位小数, 保证相邻边严格重合(不留浮点毛刺)。"""
    r = round(v + 0.0, DIGITS)
    return 0.0 if r == 0 else r


def _clamp_span(a, b, limit):
    """把一段区间夹进 [0, limit]; 越界时整体平移而不截短, 保持宽/高语义。"""
    if b - a > limit:
        return 0.0, q(limit)
    if a < 0.0:
        return 0.0, q(b - a)
    if b > limit:
        return q(limit - (b - a)), q(limit)
    return a, b


def place(blocks, cols, cfg, W, H, col_w, col_gap):
    """块 -> 输出矩形: 行内水平居中, 列自左向右, 块自下而上。

    坐标按边量化(先定左右/上下两条边, 再求宽高), 使同一字幕的相邻行盒严格
    上下相接、互不侵入, 不同字幕之间保留等于 vertical_gap 的正间隙。
    """
    side = float(cfg["side_margin"]) * float(cfg["font"])
    bottom = float(cfg["bottom_margin"]) * float(cfg["font"])
    vgap = float(cfg["vertical_gap"]) * float(cfg["font"])
    rects = []
    baseline = H - bottom
    for ci, col_blocks in enumerate(cols):
        col_x = side + ci * (col_w + col_gap)
        y_cursor = baseline
        for b in col_blocks:
            top = y_cursor - b["h"]
            for k, ln in enumerate(b["lines"]):
                qx0 = q(col_x + (col_w - ln["w"]) / 2.0)
                qx1 = q(col_x + (col_w + ln["w"]) / 2.0)
                qy0 = q(top + k * b["lh"])
                qy1 = q(top + (k + 1) * b["lh"])
                qx0, qx1 = _clamp_span(qx0, qx1, W)
                qy0, qy1 = _clamp_span(qy0, qy1, H)
                rects.append({
                    "text": ln["text"],
                    "x": qx0,
                    "y": qy0,
                    "w": q(qx1 - qx0),
                    "h": q(qy1 - qy0),
                    "font": b["font"],
                    "sid": b["sid"],
                    "line": k,
                    "nlines": len(b["lines"]),
                })
            y_cursor = top - vgap
    return rects


def _build_blocks(active, cfg, col_w, avail_h):
    blocks = []
    for sub in active:
        b = layout_block(sub["text"], cfg, col_w, avail_h)
        if b is None:
            continue
        b["sid"] = sub["sid"]
        blocks.append(b)
    return blocks


def _score(blocks, dropped, clipped, ncols):
    """越小越优: 先保证有字幕被完整显示, 再减少被裁掉的行, 最后取最少列。"""
    return (len(dropped), clipped, ncols)


def arrange(active, cfg, W, H):
    """一帧布局, 返回 (rects, info)。列数从 1 起试, 取最省列且无裁剪者。

    所有输出矩形保证落在画面内; 极端情形(列数用尽仍超高)按规范序丢尾部块并记录。
    列宽不得窄于 min_col_em*font, 否则退化为「一列一字符」, 反而更不可读。
    """
    base = float(cfg["font"])
    side = float(cfg["side_margin"]) * base
    top_m = float(cfg["top_margin"]) * base
    bot_m = float(cfg["bottom_margin"]) * base
    vgap = float(cfg["vertical_gap"]) * base
    cgap = float(cfg["column_gap"]) * base
    min_col = float(cfg["min_col_em"]) * base
    avail_h = max(0.0, H - top_m - bot_m)
    avail_w = max(0.0, W - 2.0 * side)

    best = None
    for ncols in range(1, int(cfg["max_columns"]) + 1):
        col_w = (avail_w - cgap * (ncols - 1)) / ncols
        if col_w <= 0 or (ncols > 1 and col_w < min_col - EPS):
            break
        blocks = _build_blocks(active, cfg, col_w, avail_h)
        cols, dropped = pack_columns(blocks, ncols, avail_h, vgap)
        clipped = sum(b["dropped"] for b in blocks)
        key = _score(blocks, dropped, clipped, ncols)
        if best is None or key < best[0]:
            best = (key, blocks, cols, ncols, col_w, dropped)
        if key[0] == 0 and key[1] == 0:
            break
    if best is None:
        col_w = max(avail_w, 1.0)
        blocks = _build_blocks(active, cfg, col_w, avail_h)
        cols, dropped = pack_columns(blocks, 1, avail_h, vgap)
        best = ((len(dropped), sum(b["dropped"] for b in blocks), 1),
                blocks, cols, 1, col_w, dropped)
    key, blocks, cols, ncols, col_w, dropped = best
    rects = place(blocks, cols, cfg, W, H, col_w, cgap)
    info = {
        "ncols": ncols,
        "overflow": key[1] > 0,
        "dropped": [b["sid"] for b in dropped],
        "clipped": key[1],
        "degrade": sorted({b["sid"] for b in blocks if b["shrunk"]}),
        "hard": sorted({b["sid"] for b in blocks if b["hard"]}),
    }
    return rects, info


# ---------------------------------------------------------------- 面积统计
def _inter(a, b):
    dx = min(a["x"] + a["w"], b["x"] + b["w"]) - max(a["x"], b["x"])
    dy = min(a["y"] + a["h"], b["y"] + b["h"]) - max(a["y"], b["y"])
    return dx * dy if dx > EPS and dy > EPS else 0.0


def areas(rects):
    """返回 (遮挡面积 = 矩形面积和, 重叠面积 = 两两交叠累加)。"""
    cover = math.fsum(r["w"] * r["h"] for r in rects)
    overl = 0.0
    for i in range(len(rects)):
        for j in range(i + 1, len(rects)):
            overl += _inter(rects[i], rects[j])
    return cover, overl


# ---------------------------------------------------------------- 时间窗
def frac_time(v):
    if isinstance(v, Fraction):
        return v
    if isinstance(v, bool):
        return Fraction(int(v))
    if isinstance(v, int):
        return Fraction(v)
    if isinstance(v, float):
        return Fraction(repr(v))  # 十进制字面量取有理数, 边界帧不受二进制误差影响
    return Fraction(str(v))


def canonical_subs(subs):
    """规范序只依赖 (t0,t1,text), 与输入数组顺序无关。"""
    items = []
    for s in subs:
        text = s.get("text", "")
        if not isinstance(text, str):
            text = str(text)
        items.append({"t0": frac_time(s.get("t0", 0)),
                      "t1": frac_time(s.get("t1", 0)),
                      "text": text})
    items.sort(key=lambda s: (s["t0"], s["t1"], s["text"]))
    for i, s in enumerate(items):
        s["sid"] = "s%d" % i
    return items


# ---------------------------------------------------------------- 校验
def verify(out, cfg):
    """独立校验: 不同字幕矩形像素级不重叠, 行盒在画面内, overlap_px 为 0。"""
    problems = []
    W, H = cfg["w"], cfg["h"]
    for fr in out["frames"]:
        rects = fr["rects"]
        for i in range(len(rects)):
            a = rects[i]
            if a["w"] < -EPS or a["h"] < -EPS:
                problems.append((fr["f"], "negative size", a["sid"]))
            if a["x"] < -1e-6 or a["x"] + a["w"] > W + 1e-6:
                problems.append((fr["f"], "x out of frame", a["sid"]))
            if a["y"] < -1e-6 or a["y"] + a["h"] > H + 1e-6:
                problems.append((fr["f"], "y out of frame", a["sid"]))
            for j in range(i + 1, len(rects)):
                b = rects[j]
                if _inter(a, b) <= 1e-6:
                    continue
                if a["sid"] != b["sid"]:
                    problems.append((fr["f"], "cross-sub overlap", a["sid"], b["sid"]))
                else:
                    problems.append((fr["f"], "same-sub overlap", a["sid"]))
        if abs(fr["overlap_px"]) > 1e-6:
            problems.append((fr["f"], "overlap_px != 0", fr["overlap_px"]))
    return problems


# ---------------------------------------------------------------- 主流程
def normalize(req):
    cfg = dict(DEFAULT_CFG)
    for k in list(cfg):
        if k in req and req[k] is not None:
            cfg[k] = req[k]
    cfg["fps"] = float(cfg["fps"])
    cfg["w"] = float(cfg["w"])
    cfg["h"] = float(cfg["h"])
    cfg["font"] = float(cfg["font"])
    cfg["max_columns"] = int(cfg["max_columns"])
    if cfg["fps"] <= 0:
        raise ValueError("fps must be > 0")
    if cfg["w"] <= 0 or cfg["h"] <= 0:
        raise ValueError("w/h must be > 0")
    if cfg["font"] <= 0:
        raise ValueError("font must be > 0")
    if cfg["max_columns"] < 1:
        raise ValueError("max_columns must be >= 1")
    if float(cfg["font_step"]) <= 0 or float(cfg["font_step"]) > 1:
        raise ValueError("font_step must be in (0, 1]")
    if float(cfg["line_spacing"]) <= 0:
        raise ValueError("line_spacing must be > 0")
    return cfg


def run(req, check=False):
    cfg = normalize(req)
    subs = canonical_subs(req.get("subs") or [])
    fps = frac_time(cfg["fps"])
    if req.get("dur") is not None:
        dur = frac_time(req["dur"])
    else:
        dur = max([s["t1"] for s in subs], default=Fraction(0))
    nframes = max(int(math.floor(float(dur) * cfg["fps"] + 1e-9)), 0)

    W, H = cfg["w"], cfg["h"]
    frames = []
    degrade_total = 0
    degrade_sids = set()
    hard_total = 0
    hard_sids = set()
    dropped_total = 0
    dropped_sids = set()
    clipped_total = 0
    cover_total = 0.0
    overlap_total = 0.0
    overflow_frames = 0
    max_cols_used = 0
    transitions = []

    prev_on = None
    for f in range(nframes):
        t = Fraction(f) / fps
        active = [s for s in subs if s["t0"] <= t < s["t1"]]
        rects, info = arrange(active, cfg, W, H)
        cover, overl = areas(rects)

        n_deg = sum(1 for r in rects if r["font"] < cfg["font"] - EPS)
        degrade_total += n_deg
        degrade_sids.update(info["degrade"])
        hard_total += len(info["hard"])
        hard_sids.update(info["hard"])
        dropped_total += len(info["dropped"])
        dropped_sids.update(info["dropped"])
        clipped_total += info["clipped"]
        cover_total += cover
        overlap_total += overl
        if info["overflow"]:
            overflow_frames += 1
        max_cols_used = max(max_cols_used, info["ncols"])

        on = set(s["sid"] for s in active)
        if prev_on is not None and on != prev_on:
            transitions.append({"f": f, "on": sorted(on - prev_on), "off": sorted(prev_on - on)})
        prev_on = on

        frames.append({
            "f": f,
            "t": round(float(t), DIGITS),
            "active": [s["sid"] for s in active],
            "rects": rects,
            "cover_px": round(cover, DIGITS),
            "occlusion_px": round(cover, DIGITS),
            "overlap_px": round(overl, DIGITS),
            "degrade": info["degrade"],
            "ncols": info["ncols"],
            "dropped": info["dropped"],
            "clipped": info["clipped"],
        })

    out = {
        "frames": frames,
        "degrade": degrade_total,
        "overlap_px": round(overlap_total, DIGITS),
        "occlusion_px": round(cover_total, DIGITS),
        "subs": [{"sid": s["sid"], "t0": float(s["t0"]), "t1": float(s["t1"]),
                  "text": s["text"]} for s in subs],
        "stats": {
            "frames": nframes,
            "subs": len(subs),
            "font": cfg["font"],
            "w": W, "h": H,
            "degrade_rects": degrade_total,
            "degrade_subs": sorted(degrade_sids),
            "hard_split_rects": hard_total,
            "hard_split_subs": sorted(hard_sids),
            "dropped_subs": sorted(dropped_sids),
            "dropped_blocks": dropped_total,
            "clipped_lines": clipped_total,
            "max_columns_used": max_cols_used,
            "overflow_frames": overflow_frames,
            "cover_px": round(cover_total, DIGITS),
            "cover_ratio": round(cover_total / (W * H * nframes), DIGITS) if nframes else 0.0,
            "overlap_px": round(overlap_total, DIGITS),
            "transitions": transitions,
        },
    }
    if check:
        problems = verify(out, cfg)
        if problems:
            raise AssertionError("layout invariant violated: %r" % (problems[:5],))
    return out


def main(argv):
    if len(argv) < 3:
        sys.stderr.write("usage: %s req.json output.json [--check]\n" % argv[0])
        return 2
    with open(argv[1], "r", encoding="utf-8") as fh:
        req = json.load(fh)
    out = run(req, check="--check" in argv[3:])
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(out, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
