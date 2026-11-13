#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""YUV<->RGB 色彩转换（修复版）。

修复点相对初版：
  1. 矩阵常量按标准严格区分（bt601 / bt709 / bt2020 各自的 Kr、Kb），
     不再出现 bt709 路径误用 bt601 常量。
  2. Cb/Cr 增益改为标准公式 0.5/(1-Kb)、0.5/(1-Kr)（初版误写 1+K）。
  3. 范围缩放与矩阵变换严格分层、顺序固定：
        输入码值 -> 归一化 full 域 [0,1] -> 矩阵线性变换 ->
        按 *目标* 范围缩放编码 -> 半整数向上舍入 -> 截断到 [0,255]
     limited/full 缩放只出现在矩阵的两侧，绝不插入矩阵中间。
  4. 舍入统一为 half-up（半整数向 +inf），不再使用 Python 银行家 round()。
  5. 新增：逐像素转换追踪与范围标记、LUT 定点实现路径、色度二次采样
     位置/相位约定处理、跨标准混用告警、容差内往返通道。

仅依赖 Python 3.9+ 标准库，CLI 保持兼容：
    python3 convert.py input.json output.json
"""
import json
import math
import sys

# ---------------------------------------------------------------------------
# 一、矩阵常量（只与标准有关，与范围 limited/full 完全解耦）
# ---------------------------------------------------------------------------
MATRIX_CONSTANTS = {
    #          Kr      Kb      标准来源
    "bt601":  (0.299,  0.114),   # ITU-R BT.601
    "bt709":  (0.2126, 0.0722),  # ITU-R BT.709
    "bt2020": (0.2627, 0.0593),  # ITU-R BT.2020
}

# ---------------------------------------------------------------------------
# 二、范围常量（只与 limited/full 有关，与标准完全解耦）
# ---------------------------------------------------------------------------
RGB_FULL = (0, 255)       # full range RGB 码值区间
RGB_LIMITED = (16, 235)   # limited range RGB 标称区间（footroom/headroom 码仍可出现）
Y_LIMITED = (16, 235)     # limited range Y
C_LIMITED = (16, 240)     # limited range Cb/Cr，中性值 128
CHROMA_NEUTRAL = 128
CODE_MIN, CODE_MAX = 0, 255

# 8-bit 往返通道容差（码值单位，即 2/255）
ROUNDTRIP_TOLERANCE = 2

# LUT 定点路径的小数位数（Q16）
FP_SHIFT = 16
FP_SCALE = 1 << FP_SHIFT


def coeffs(std):
    """返回某标准的矩阵系数：Kr, Kg, Kb, Cb 增益, Cr 增益。

    Cb/Cr 增益严格按 0.5/(1-Kb)、0.5/(1-Kr)。
    """
    kr, kb = MATRIX_CONSTANTS[std]
    kg = 1.0 - kr - kb
    gain_cb = 0.5 / (1.0 - kb)
    gain_cr = 0.5 / (1.0 - kr)
    return kr, kg, kb, gain_cb, gain_cr


# ---------------------------------------------------------------------------
# 三、舍入与截断（half-up，对负值同样表示半整数向 +inf）
# ---------------------------------------------------------------------------
# 半整数网格吸附容差：仅用于修正浮点和（如 kr+kg+kb=1.0000000000000002）
# 在真正的半整数码值边界上造成的 ~1e-14 级抖动，远小于任何真实量化间隔。
SNAP_EPS = 1e-9


def half_up(x):
    """半整数向上舍入：floor(x + 0.5)，对任意符号一致。

    若 x 距某个半整数（k+0.5）的偏差不超过 SNAP_EPS，先吸附到该半整数，
    消除浮点累加漂移导致的舍入翻转。
    """
    n = math.floor(x)
    if abs((x - n) - 0.5) <= SNAP_EPS:
        return n + 1
    return math.floor(x + 0.5)


def clip_code(x):
    """最终码值统一截断到 [0,255]（README 契约）。"""
    return CODE_MAX if x > CODE_MAX else CODE_MIN if x < CODE_MIN else x


def encode_code(x):
    """缩放后的统一出口：half-up 舍入 + [0,255] 截断，返回 (码值, 是否被截断)。"""
    rounded = half_up(x)
    clipped = rounded != clip_code(rounded)
    return clip_code(rounded), clipped


# ---------------------------------------------------------------------------
# 四、范围缩放层：码值 <-> 归一化 full 域（矩阵永远在归一化 full 域内进行）
# ---------------------------------------------------------------------------
def norm_rgb(c, rng):
    """RGB 码值 -> 归一化 [0,1] 域。"""
    return c / 255.0 if rng == "full" else (c - 16) / 219.0


def norm_y(c, rng):
    """Y 码值 -> 归一化 [0,1] 域。"""
    return norm_rgb(c, rng)  # full: /255；limited: (c-16)/219，公式同构


def norm_chroma(c, rng):
    """Cb/Cr 码值 -> 零中心归一化域（中性 128 -> 0.0）。"""
    if rng == "full":
        return c / 255.0 - 0.5
    return (c - CHROMA_NEUTRAL) / 224.0


def scale_y(y01, rng):
    """归一化 Y [0,1] -> 目标范围码值（舍入前）。"""
    return y01 * 255.0 if rng == "full" else 16.0 + 219.0 * y01


def scale_chroma(c01, rng):
    """归一化 Cb/Cr（[0,1] 形式，0.5 为中性）-> 目标范围码值（舍入前）。"""
    return c01 * 255.0 if rng == "full" else 16.0 + 224.0 * c01


def scale_rgb(v01, rng):
    """归一化 R'G'B' [0,1] -> 目标范围码值（舍入前）。"""
    return v01 * 255.0 if rng == "full" else 16.0 + 219.0 * v01


def _range_marker(ch, code, nominal):
    """limited 范围下，落在标称区间外的码值标记 footroom/headroom。"""
    lo, hi = nominal
    if code < lo:
        return "footroom:%s" % ch
    if code > hi:
        return "headroom:%s" % ch
    return None


# ---------------------------------------------------------------------------
# 五、矩阵路径（浮点，参考实现）
# ---------------------------------------------------------------------------
def rgb_to_yuv(r, g, b, std, rgb_rng, yuv_rng, trace=False):
    """RGB -> YUV。返回 [Y,Cb,Cr] 码值；trace=True 时附转换追踪 dict。"""
    kr, kg, kb, gain_cb, gain_cr = coeffs(std)

    # 步骤 1：输入侧范围反缩放，进入归一化 full 域
    rn, gn, bn = norm_rgb(r, rgb_rng), norm_rgb(g, rgb_rng), norm_rgb(b, rgb_rng)

    # 步骤 2：归一化 full 域内的标准矩阵（与范围无关）
    y = kr * rn + kg * gn + kb * bn
    cb = gain_cb * (bn - y) + 0.5
    cr = gain_cr * (rn - y) + 0.5

    # 步骤 3：按目标 YUV 范围缩放、half-up、截断 [0,255]
    pre = (scale_y(y, yuv_rng), scale_chroma(cb, yuv_rng), scale_chroma(cr, yuv_rng))
    out, marks = [], []
    for ch, val, nominal in (
        ("y", pre[0], Y_LIMITED),
        ("cb", pre[1], C_LIMITED),
        ("cr", pre[2], C_LIMITED),
    ):
        code, clipped = encode_code(val)
        out.append(code)
        if clipped:
            marks.append("clipped:%s" % ch)
        if yuv_rng == "limited":
            m = _range_marker(ch, code, nominal)
            if m:
                marks.append(m)

    if trace:
        return out, {
            "in": [r, g, b],
            "in_range": rgb_rng,
            "normalized_rgb": [rn, gn, bn],
            "matrix_yuv_full_range": [y, cb, cr],
            "pre_round": list(pre),
            "out": out,
            "out_range": yuv_rng,
            "markers": marks,
        }
    return out


def yuv_to_rgb(y, cb, cr, std, yuv_rng, rgb_rng, trace=False):
    """YUV -> RGB。返回 [R,G,B] 码值；trace=True 时附转换追踪 dict。

    逆矩阵可能产生负的或 >1 的 R'G'B' 中间值（超色域 YUV 码组），
    中间值不提前钳位，仅在最终编码后按规则截断到 [0,255]，并打标记。
    """
    kr, kg, kb, _, _ = coeffs(std)

    # 步骤 1：YUV 输入侧反缩放，进入归一化 full 域
    yn = norm_y(y, yuv_rng)
    cb0 = norm_chroma(cb, yuv_rng)   # 零中心
    cr0 = norm_chroma(cr, yuv_rng)

    # 步骤 2：归一化 full 域内的逆矩阵
    rn = yn + 2.0 * (1.0 - kr) * cr0
    bn = yn + 2.0 * (1.0 - kb) * cb0
    gn = (yn - kr * rn - kb * bn) / kg

    out_of_gamut = min(rn, gn, bn) < 0.0 or max(rn, gn, bn) > 1.0

    # 步骤 3：按目标 RGB 范围缩放、half-up、截断 [0,255]
    pre = (scale_rgb(rn, rgb_rng), scale_rgb(gn, rgb_rng), scale_rgb(bn, rgb_rng))
    out, marks = [], []
    for ch, val in zip(("r", "g", "b"), pre):
        code, clipped = encode_code(val)
        out.append(code)
        if clipped:
            marks.append("clipped:%s" % ch)
        if rgb_rng == "limited":
            m = _range_marker(ch, code, RGB_LIMITED)
            if m:
                marks.append(m)
    if out_of_gamut:
        marks.append("out-of-gamut")

    if trace:
        return out, {
            "in": [y, cb, cr],
            "in_range": yuv_rng,
            "normalized_yuv": [yn, cb0, cr0],
            "linear_rgb_full_range": [rn, gn, bn],
            "pre_round": list(pre),
            "out": out,
            "out_range": rgb_rng,
            "markers": marks,
        }
    return out


# ---------------------------------------------------------------------------
# 六、LUT 定点路径（Q16 整数查表，独立于浮点矩阵路径的运算方式）
#
# 所有归一化、系数都预先做成 256 项/系数表，主路径只做整数乘加与
# half-up 整数除法；与矩阵路径在相同契约下结果应一致（至多在半整数
# 舍入边界相差 1 个码值）。
# ---------------------------------------------------------------------------
def _div_half_up(num, den):
    """整数 half-up 除法（den 为偶数时成立，半整数向 +inf）。"""
    return (num + den // 2) // den


class LutPath:
    def __init__(self):
        fp = FP_SCALE
        # 输入归一化查表（码值 -> Q16 归一化）
        self.t_rgb_full = [_div_half_up(c * fp, 255) for c in range(256)]
        self.t_rgb_lim = [_div_half_up((c - 16) * fp, 219) for c in range(256)]
        self.t_y_full = self.t_rgb_full
        self.t_y_lim = self.t_rgb_lim
        # 色度零中心归一化表
        self.t_c_full = [_div_half_up(c * fp, 255) - fp // 2 for c in range(256)]
        self.t_c_lim = [_div_half_up((c - CHROMA_NEUTRAL) * fp, 224) for c in range(256)]
        # 各标准的 Q16 系数表
        self.std_tab = {}
        for std, (kr, kb) in MATRIX_CONSTANTS.items():
            kg = 1.0 - kr - kb
            self.std_tab[std] = {
                "kr": _div_half_up(round(kr * fp), 1),
                "kg": _div_half_up(round(kg * fp), 1),
                "kb": _div_half_up(round(kb * fp), 1),
                "gcb": _div_half_up(round(0.5 / (1.0 - kb) * fp), 1),
                "gcr": _div_half_up(round(0.5 / (1.0 - kr) * fp), 1),
                "r_cr": _div_half_up(round(2.0 * (1.0 - kr) * fp), 1),
                "b_cb": _div_half_up(round(2.0 * (1.0 - kb) * fp), 1),
            }

    def _norm_rgb(self, c, rng):
        return self.t_rgb_full[c] if rng == "full" else self.t_rgb_lim[c]

    def _encode(self, v_fp, rng, low, span):
        if rng == "full":
            return clip_code(_div_half_up(v_fp * 255, FP_SCALE))
        return clip_code(low + _div_half_up(v_fp * span, FP_SCALE))

    def rgb_to_yuv(self, r, g, b, std, rgb_rng, yuv_rng):
        t = self.std_tab[std]
        rn = self._norm_rgb(r, rgb_rng)
        gn = self._norm_rgb(g, rgb_rng)
        bn = self._norm_rgb(b, rgb_rng)
        y_fp = (t["kr"] * rn + t["kg"] * gn + t["kb"] * bn) // FP_SCALE
        # cb = 0.5 + gcb*(bn - y)，0.5 即 FP_SCALE//2
        cb_fp = FP_SCALE // 2 + (t["gcb"] * (bn - y_fp)) // FP_SCALE
        cr_fp = FP_SCALE // 2 + (t["gcr"] * (rn - y_fp)) // FP_SCALE
        return [
            self._encode(y_fp, yuv_rng, 16, 219),
            self._encode(cb_fp, yuv_rng, 16, 224),
            self._encode(cr_fp, yuv_rng, 16, 224),
        ]

    def yuv_to_rgb(self, y, cb, cr, std, yuv_rng, rgb_rng):
        t = self.std_tab[std]
        yn = (self.t_y_full[y] if yuv_rng == "full" else self.t_y_lim[y])
        cb0 = (self.t_c_full[cb] if yuv_rng == "full" else self.t_c_lim[cb])
        cr0 = (self.t_c_full[cr] if yuv_rng == "full" else self.t_c_lim[cr])
        rn = yn + (t["r_cr"] * cr0) // FP_SCALE
        bn = yn + (t["b_cb"] * cb0) // FP_SCALE
        # gn = (yn - kr*rn - kb*bn) / kg，用整数乘加后再除 kg
        num = yn * FP_SCALE - t["kr"] * rn - t["kb"] * bn
        gn = num // t["kg"] if t["kg"] else 0
        return [
            self._encode(rn, rgb_rng, 16, 219),
            self._encode(gn, rgb_rng, 16, 219),
            self._encode(bn, rgb_rng, 16, 219),
        ]


_LUT = None


def _lut():
    global _LUT
    if _LUT is None:
        _LUT = LutPath()
    return _LUT


# 路径分发：matrix=浮点矩阵（默认），lut=Q16 查表
def convert_pixel(direction, pix, std, rgb_rng, yuv_rng, impl="matrix", trace=False):
    if direction == "rgb-to-yuv":
        if impl == "lut":
            if trace:
                raise ValueError("lut 路径不产生 trace，请使用 matrix 路径")
            return _lut().rgb_to_yuv(*pix, std, rgb_rng, yuv_rng), None
        return rgb_to_yuv(*pix, std, rgb_rng, yuv_rng, trace=trace)
    if direction == "yuv-to-rgb":
        if impl == "lut":
            if trace:
                raise ValueError("lut 路径不产生 trace，请使用 matrix 路径")
            return _lut().yuv_to_rgb(*pix, std, yuv_rng, rgb_rng), None
        return yuv_to_rgb(*pix, std, yuv_rng, rgb_rng, trace=trace)
    raise ValueError("unknown direction: %r" % direction)


# ---------------------------------------------------------------------------
# 七、色度二次采样：位置与相位约定
#
# 色度样本在全分辨率网格上的水平/垂直位置由 siting 决定：
#   "left"   （MPEG-1 / JPEG 共点，co-sited）：相位 0，
#             色度样本 j 落在全分辨率坐标 2j；
#   "center" （MPEG-2 / 蓝光，居中）：相位 0.5，
#             色度样本 j 落在全分辨率坐标 2j+0.5。
# 下采样按相位做点采样或盒式平均；上采样按同相位做双线性重建，
# 边界一律复制边缘样本。全部在整数码值平面上进行，平均用 half-up。
# ---------------------------------------------------------------------------
def _pair_down(a, b, siting):
    if siting == "left":
        return a                       # 相位 0：直接取共点样本
    return half_up((a + b) / 2.0)      # 相位 0.5：盒式平均


def subsample_plane(plane, width, height, factor_x, factor_y, siting):
    """对单个码值平面做 1x/2x 水平、垂直二次采样，返回 (小平面, 小宽, 小高)。"""
    if factor_x not in (1, 2) or factor_y not in (1, 2):
        raise ValueError("仅支持 1x/2x 二次采样")
    if siting not in ("left", "center"):
        raise ValueError("siting 仅支持 left/center")
    if factor_x == 1 and factor_y == 1:
        return list(plane), width, height

    # 先水平
    if factor_x == 2:
        tmp, tw = [], (width + 1) // 2
        for row in range(height):
            line = plane[row * width:(row + 1) * width]
            for j in range(tw):
                a = line[2 * j]
                b = line[2 * j + 1] if 2 * j + 1 < width else line[2 * j]
                tmp.append(_pair_down(a, b, siting))
    else:
        tmp, tw = list(plane), width

    # 再垂直
    if factor_y == 2:
        th = (height + 1) // 2
        out = []
        for i in range(th):
            row0 = tmp[2 * i * tw:(2 * i + 1) * tw]
            if 2 * i + 1 < height:
                row1 = tmp[(2 * i + 1) * tw:(2 * i + 2) * tw]
            else:
                row1 = row0
            if siting == "left":
                out.extend(row0)
            else:
                out.extend(half_up((a + b) / 2.0) for a, b in zip(row0, row1))
        return out, tw, th
    return tmp, tw, height


def upsample_plane(plane, cw, ch, width, height, factor_x, factor_y, siting):
    """按相同相位约定把色度平面双线性重建回全分辨率。"""
    phase = 0.0 if siting == "left" else 0.5

    def sample(grid, gw, x):
        # 色度样本 j 位于全分辨率坐标 2j+phase；对坐标 x 做线性插值
        pos = (x - phase) / 2.0
        j0 = int(math.floor(pos))
        t = pos - j0
        j1 = j0 + 1
        j0c = min(max(j0, 0), gw - 1)
        j1c = min(max(j1, 0), gw - 1)
        v = grid[j0c] * (1.0 - t) + grid[j1c] * t
        return clip_code(half_up(v))

    # 先水平重建
    if factor_x == 2:
        tmp = []
        for i in range(ch):
            line = plane[i * cw:(i + 1) * cw]
            tmp.extend(sample(line, cw, x) for x in range(width))
        hw = width
    else:
        tmp, hw = list(plane), width

    # 再垂直重建
    if factor_y == 2:
        cols = [[tmp[i * hw + x] for i in range(ch)] for x in range(hw)]
        out = [0] * (width * height)
        for x in range(hw):
            col = cols[x]
            for y in range(height):
                out[y * width + x] = sample(col, ch, y)
        return out
    return tmp


CHROMA_FACTORS = {
    "444": (1, 1),
    "422": (2, 1),
    "420": (2, 2),
}


# ---------------------------------------------------------------------------
# 八、跨标准混用检测
# ---------------------------------------------------------------------------
def collect_standards(req):
    """收集请求中所有声明的 standard：请求级、meta、op 级 meta、chain 各跳。"""
    found = set()
    if req.get("standard"):
        found.add(req["standard"])
    meta = req.get("meta") or {}
    if meta.get("standard"):
        found.add(meta["standard"])
    for op in req.get("ops", []):
        opm = op.get("meta") or {}
        if opm.get("standard"):
            found.add(opm["standard"])
    for hop in req.get("chain", []) or []:
        if hop.get("standard"):
            found.add(hop["standard"])
    return found


# ---------------------------------------------------------------------------
# 九、请求处理 / CLI
# ---------------------------------------------------------------------------
def _frame_rgb_to_yuv(op, req, impl, want_trace):
    """帧模式 RGB->YUV：逐像素矩阵 + 可选色度二次采样。"""
    width = op["width"]
    pixels = op["pixels"]
    if width <= 0 or len(pixels) % width != 0:
        raise ValueError("width 与像素数不匹配")
    height = len(pixels) // width
    y_plane, cb_plane, cr_plane, traces = [], [], [], []
    for pix in pixels:
        out, tr = convert_pixel("rgb-to-yuv", pix, req["standard"],
                                req["rgb_range"], req["yuv_range"], impl, want_trace)
        y_plane.append(out[0])
        cb_plane.append(out[1])
        cr_plane.append(out[2])
        if want_trace:
            traces.append(tr)
    entry = {"pixels": [[y_plane[i], cb_plane[i], cr_plane[i]]
                        for i in range(len(pixels))]}
    chroma = op.get("chroma") or {}
    scheme = chroma.get("subsampling", "444")
    siting = chroma.get("siting", "center")
    fx, fy = CHROMA_FACTORS[scheme]
    if (fx, fy) != (1, 1):
        cb_s, cw, ch = subsample_plane(cb_plane, width, height, fx, fy, siting)
        cr_s, _, _ = subsample_plane(cr_plane, width, height, fx, fy, siting)
        entry["frame"] = {
            "width": width, "height": height,
            "y": y_plane,
            "cb": cb_s, "cr": cr_s,
            "chroma_width": cw, "chroma_height": ch,
            "subsampling": scheme, "siting": siting,
        }
    if want_trace:
        entry["trace"] = traces
    return entry


def _frame_yuv_to_rgb(op, req, impl):
    """帧模式 YUV->RGB：按相位上采样色度后逐像素逆矩阵。"""
    fr = op["frame"]
    width, height = fr["width"], fr["height"]
    cw, ch = fr["chroma_width"], fr["chroma_height"]
    scheme = fr.get("subsampling", "444")
    siting = fr.get("siting", "center")
    fx, fy = CHROMA_FACTORS[scheme]
    cb_full = upsample_plane(fr["cb"], cw, ch, width, height, fx, fy, siting)
    cr_full = upsample_plane(fr["cr"], cw, ch, width, height, fx, fy, siting)
    pixels = []
    for i, yv in enumerate(fr["y"]):
        out, _ = convert_pixel("yuv-to-rgb", [yv, cb_full[i], cr_full[i]],
                               req["standard"], req["rgb_range"],
                               req["yuv_range"], impl, False)
        pixels.append(out)
    return {"pixels": pixels}


def process_request(req):
    std = req["standard"]
    if std not in MATRIX_CONSTANTS:
        raise ValueError("unknown standard: %r" % std)
    impl = req.get("impl", "matrix")
    if impl not in ("matrix", "lut"):
        raise ValueError("impl 仅支持 matrix/lut")
    want_trace = bool(req.get("trace"))

    out = {"ops": [], "warnings": []}

    # 跨标准混用：请求级/像素元数据/chain 中出现两个及以上 standard
    if len(collect_standards(req)) > 1:
        out["warnings"].append("mixed-standard")

    for op in req["ops"]:
        if "frame" in op and op["direction"] == "yuv-to-rgb":
            entry = _frame_yuv_to_rgb(op, req, impl)
        elif "width" in op and op["direction"] == "rgb-to-yuv":
            entry = _frame_rgb_to_yuv(op, req, impl, want_trace)
        else:
            pixels, traces = [], []
            for pix in op["pixels"]:
                converted, tr = convert_pixel(
                    op["direction"], pix, std,
                    req["rgb_range"], req["yuv_range"], impl, want_trace)
                pixels.append(converted)
                if want_trace:
                    traces.append(tr)
            entry = {"pixels": pixels}
            if want_trace:
                entry["trace"] = traces

        if req.get("roundtrip"):
            back = []
            for q in entry["pixels"]:
                inv, _ = convert_pixel(
                    "yuv-to-rgb" if op["direction"] == "rgb-to-yuv" else "rgb-to-yuv",
                    q, std, req["rgb_range"], req["yuv_range"], "matrix", False)
                back.append(inv)
            src = op["pixels"]
            max_err = max(abs(a - b)
                          for s, t in zip(src, back) for a, b in zip(s, t))
            entry["roundtrip"] = {
                "max_err": max_err,
                "tolerance": ROUNDTRIP_TOLERANCE,
                "within_tolerance": max_err <= ROUNDTRIP_TOLERANCE,
            }
        out["ops"].append(entry)
    return out


def main(argv):
    if len(argv) != 3:
        print("usage: python3 convert.py input.json output.json", file=sys.stderr)
        return 2
    with open(argv[1], "r", encoding="utf-8") as f:
        req = json.load(f)
    out = process_request(req)
    with open(argv[2], "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
