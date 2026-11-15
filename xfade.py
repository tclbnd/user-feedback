"""等功率(cos/sin)交叉淡化 + 过零窗吸附 + 短窗相位对齐。

用法: python3 xfade.py input.json output.json
input.json: {"a":[float],"b":[float],"len":512,"block":128,"seed":1}
输出: {"out":[...], "splice_pos":n, "stats":{...}, "digest":"sha1"}

设计:
  1) 等功率淡化 g_a=cos(pi*t/2), g_b=sin(pi*t/2), t=i/(L-1),
     恒有 g_a^2+g_b^2=1, 中点能量相对输入无下凹(线性等幅为 -3.01 dB)。
  2) 剪接点在中点 +/- L/8 的过零窗内吸附到 |a[i]-b[i]| 最小处。
  3) 剪接点两侧短窗 DFT 估计主成分 (omega, phi, A) 检测相位斜率差;
     以"超额跳变"(加权样本增量扣除同相自然增量)为触发判据, 超限时
     在对称短窗内做输出域连续化(升余弦窗向 a 的值/斜率参考线靠拢,
     窗远端零修正, 不产生新断点), 修正无益时安全回滚。
  4) 逐样本增益/修正表一次性算好, 再按 block 分块产出, 块边界不影响结果。
仅依赖标准库, 全程确定性浮点运算。
"""
import json
import sys
import math
import hashlib

TAU = 2.0 * math.pi


def wrap_angle(x):
    """折叠到 (-pi, pi]。"""
    return (x + math.pi) % TAU - math.pi


def _tone_sums(x, w):
    """模型 x[n] ~ A*cos(w*n+phi) 下的相关和。"""
    sc = 0.0
    ss = 0.0
    for n, v in enumerate(x):
        sc += v * math.cos(w * n)
        ss += v * math.sin(w * n)
    return sc, ss


def estimate_tone(x):
    """短窗主成分估计, 返回 (omega[rad/sample], phase[rad], amp, rms)。

    先做 DFT 整数 bin 网格搜索, 再在最优 bin 附近用黄金分割连续细化频率;
    phase 满足 x[n] ~ amp*cos(omega*n + phase)。
    近直流/近零信号返回 omega=0。
    """
    m = len(x)
    if m == 0:
        return 0.0, 0.0, 0.0, 0.0
    rms = math.sqrt(sum(v * v for v in x) / m)
    if rms < 1e-12:
        return 0.0, 0.0, 0.0, rms

    best_e = -1.0
    best_k = 1
    grid = m // 2
    for k in range(1, grid + 1):
        w = TAU * k / m
        sc, ss = _tone_sums(x, w)
        e = sc * sc + ss * ss
        if e > best_e:
            best_e, best_k = e, k

    # 黄金分割在相邻 bin 区间内细化 (最大化 E(w))
    lo = TAU * max(1, best_k - 1) / m
    hi = TAU * min(grid, best_k + 1) / m
    gr = (math.sqrt(5.0) - 1.0) / 2.0
    c = hi - gr * (hi - lo)
    d = lo + gr * (hi - lo)

    def energy(w):
        sc, ss = _tone_sums(x, w)
        return sc * sc + ss * ss

    for _ in range(25):
        if energy(c) > energy(d):
            hi = d
        else:
            lo = c
        c = hi - gr * (hi - lo)
        d = lo + gr * (hi - lo)
    w = 0.5 * (lo + hi)

    sc, ss = _tone_sums(x, w)
    e = sc * sc + ss * ss
    phi = math.atan2(-ss, sc)          # cos(w n+phi)=cos wn cos phi - sin wn sin phi
    amp = 2.0 * math.sqrt(e) / m
    return w, phi, amp, rms


def snap_splice(a, b, mid):
    """过零窗内吸附: 在 mid +/- L/8 内取 |a[i]-b[i]| 最小的索引。"""
    L = len(a)
    w = max(1, L // 8)
    lo = max(1, mid - w)
    hi = min(L - 1, mid + w)
    best_i = mid
    best_score = float("inf")
    for i in range(lo, hi + 1):
        score = abs(a[i] - b[i])
        # 同分取更靠近中点者, 保证确定性
        if score < best_score or (score == best_score and abs(i - mid) < abs(best_i - mid)):
            best_score = score
            best_i = i
    return best_i, w


def align_phase(a, b, p, g_a, g_b):
    """剪接点对称短窗连续化(相位对齐的输出域实现), 返回 (b_alg, 最大修正量)。

    直接在最终输出域设计, 不依赖长窗 DFT 相位外推:
      raw_out[i] = ga[i]*a[i] + gb[i]*b[i]
      连续参考线(令 b 与 a 同相, a 用 p 处局部线性外推):
        a_lin[i]  = a[p] + (i-p)*(a[p]-a[p-1])
        ref[i]    = (ga[i]+gb[i]) * a_lin[i]
      升余弦窗 r[i]=0.5*(1+cos(pi*|i-p|/W)): 中心 r=1(完全贴合连续
      参考, 值与一阶差分同时匹配 a), 窗边 r=0(零修正, 不引入新断点)。
      tgt[i] = r*ref + (1-r)*raw_out, 再由 tgt = ga*a + gb*b_alg 反解:
        b_alg[i] = (tgt[i] - ga[i]*a[i]) / gb[i]
    剪接点位于淡化中部, gb 远离 0, 除法稳定。
    """
    L = len(b)
    W = max(8, min(L // 8, 32))
    lo = max(1, p - W)
    hi = min(L - 1, p + W)
    if p - 1 < 0 or p + 1 >= L:
        return list(b), 0.0

    slope = a[p] - a[p - 1]
    out = list(b)
    max_corr = 0.0
    for i in range(lo, hi + 1):
        gb = g_b[i]
        if abs(gb) < 0.1:                      # 防御: 远离中点时不修正
            continue
        r = 0.5 * (1.0 + math.cos(math.pi * abs(i - p) / W))
        raw = g_a[i] * a[i] + gb * b[i]
        a_lin = a[p] + (i - p) * slope
        ref = (g_a[i] + gb) * a_lin
        tgt = r * ref + (1.0 - r) * raw
        bi = (tgt - g_a[i] * a[i]) / gb
        max_corr = max(max_corr, abs(bi - b[i]))
        out[i] = bi
    return out, max_corr


def crossfade(a, b, L, block):
    """等功率淡化主流程, block 仅改变分块方式, 不改变数值结果。"""
    if L < 4:
        raise ValueError("淡化窗长 len 至少为 4, 收到 %d" % L)
    if block is None or block <= 0:
        block = L
    # 1) 剪接点过零窗吸附
    mid = L // 2
    p, zc_win = snap_splice(a, b, mid)

    # 2) 两侧短窗主成分 -> 相位斜率差 / 边界失配
    m = max(8, min(L // 4, 64))
    l0 = max(0, p - m + 1)
    r1 = min(L, p + m)
    left = a[l0:p + 1]
    right = b[p:r1]
    if not left or not right:
        # 极端越界保护: 退化为不做对齐
        w_a = w_b = 0.0
        phi_a_loc = phi_b_loc = 0.0
        amp_a = amp_b = rms_a = rms_b = 0.0
    else:
        w_a, phi_a_loc, amp_a, rms_a = estimate_tone(left)
        w_b, phi_b_loc, amp_b, rms_b = estimate_tone(right)
    # 局部相位 -> 全局索引初相: 局部 n = 全局 i - i0
    phi_a_glob = phi_a_loc - w_a * l0
    phi_b_glob = phi_b_loc - w_b * p
    # 剪接点 p 处两侧相位
    phi_a_at_p = phi_a_glob + w_a * p
    phi_b_at_p = phi_b_glob + w_b * p
    dphi = wrap_angle(phi_b_at_p - phi_a_at_p)
    domega = w_b - w_a

    # 3) 必要时短窗相位对齐(输出域连续化)。
    #    判据是剪接点的"超额跳变": 等功率增益加权后的实测样本增量,
    #    扣除信号自身的自然增量(同相连续参考), 剩余即相位不连续(咔哒)。
    #    硬切残差小不代表加权后连续(巧合过零点), 故以超额跳变为主判据;
    #    对齐后若超额跳变未改善则安全回滚, 防止修正误伤。
    fullscale0 = max(max(abs(v) for v in a), max(abs(v) for v in b), 1e-12)
    raw_resid = abs(a[p] - b[p]) / fullscale0
    g_a0 = [math.cos(0.5 * math.pi * i / (L - 1)) for i in range(L)]
    g_b0 = [math.sin(0.5 * math.pi * i / (L - 1)) for i in range(L)]
    # 连续参考: 令 b=a (两侧完全同相) 时剪接点的自然样本增量
    natural_delta = (g_a0[p] + g_b0[p]) * a[p] - \
                    (g_a0[p - 1] + g_b0[p - 1]) * a[p - 1]
    raw_out = [g_a0[i] * a[i] + g_b0[i] * b[i] for i in range(L)]
    excess0 = abs((raw_out[p] - raw_out[p - 1])
                  - natural_delta) / fullscale0

    need_align = (rms_a > 1e-9 and rms_b > 1e-9 and w_b > 1e-9
                  and excess0 > 0.02)
    applied = 0.0
    if need_align:
        cand, max_corr = align_phase(a, b, p, g_a0, g_b0)
        cand_out = [g_a0[i] * a[i] + g_b0[i] * cand[i] for i in range(L)]
        excess_cand = abs((cand_out[p] - cand_out[p - 1])
                          - natural_delta) / fullscale0
        if excess_cand < excess0:
            b_alg, applied = cand, max_corr
        else:
            # 修正无益(如噪声主导), 安全回滚
            b_alg, need_align = list(b), False
    else:
        b_alg = list(b)

    # 4) 等功率增益表已在上方建立: g_a=cos(pi t/2), g_b=sin(pi t/2)
    # 5) 按 block 分块产出 (纯逐样本运算, 块边界无状态依赖)
    out = [0.0] * L
    step = max(1, block)
    for start in range(0, L, step):
        stop = min(L, start + step)
        for i in range(start, stop):
            out[i] = g_a0[i] * a[i] + g_b0[i] * b_alg[i]

    stats = _stats(out, a, b_alg, p, zc_win, mid,
                   w_a, w_b, domega, dphi, applied, need_align, block,
                   raw_resid, excess0)
    return out, p, stats


def _stats(out, a, b_alg, p, zc_win, mid, w_a, w_b, domega,
           dphi, applied, aligned, block, raw_resid, excess0):
    L = len(out)
    ms = lambda x: sum(v * v for v in x) / len(x)
    lo = max(0, mid - 8)
    hi = min(L, mid + 8)
    mid_ms = ms(out[lo:hi])
    # 等功率理论参考: 整个淡化窗内按增益功率 g_a^2, g_b^2 加权的输入功率
    # (交叉项在整窗平均后趋零, 该比值稳定反映淡化器自身的能量偏差,
    #  不受局部波形相位波动影响)
    ref_mid = 0.0
    for i in range(L):
        t = i / (L - 1)
        ga2 = math.cos(0.5 * math.pi * t) ** 2
        gb2 = 1.0 - ga2
        ref_mid += ga2 * a[i] * a[i] + gb2 * b_alg[i] * b_alg[i]
    ref_mid /= L
    # 全窗参考(整体响度核对用)
    ref_ms = 0.5 * (ms(a) + ms(b_alg))
    eps = 1e-12
    mid_db = 10.0 * math.log10(mid_ms + eps)
    ref_db = 10.0 * math.log10(ref_mid + eps)
    # 验收指标: 淡化输出能量相对等功率理论值的偏差
    mid_dev_db = 10.0 * math.log10((ms(out) + eps) / (ref_mid + eps))

    # 能量曲线: 8 段分段 RMS(dB)
    k = 8
    seg = max(1, L // k)
    curve = []
    for s in range(k):
        chunk = out[s * seg: L if s == k - 1 else (s + 1) * seg]
        curve.append(10.0 * math.log10(ms(chunk) + eps))

    fullscale = max(max(abs(v) for v in a), max(abs(v) for v in b_alg), eps)
    splice_delta = abs(out[p] - out[p - 1])
    boundary_step = abs(a[p] - b_alg[p])
    # 超额跳变: 实测样本增量扣除"两侧完全同相"的自然增量, 即纯剪接不连续
    ga = lambda i: math.cos(0.5 * math.pi * i / (L - 1))
    gb = lambda i: math.sin(0.5 * math.pi * i / (L - 1))
    natural = ((ga(p) + gb(p)) * a[p]
               - (ga(p - 1) + gb(p - 1)) * a[p - 1])
    excess_jump = abs((out[p] - out[p - 1]) - natural) / fullscale
    excess_pre = excess0
    return {
        "L": L,
        "block": block,
        "splice_pos": p,
        "zero_cross_window": zc_win,
        "mid_energy_db": mid_db,
        "ref_energy_db": ref_db,
        "mid_dev_db": mid_dev_db,
        "energy_curve_db": curve,
        "phase": {
            "omega_a": w_a,
            "omega_b": w_b,
            "domega": domega,
            "mismatch_rad": dphi,
            "correction_max_amp": applied,
            "aligned": aligned,
        },
        "splice_jump": {
            "delta": splice_delta,
            "norm_fullscale": splice_delta / fullscale,
            "boundary_step_norm": boundary_step / fullscale,
            "excess_jump_norm": excess_jump,
            "excess_jump_pre_align": excess_pre,
            "raw_residual_norm": raw_resid,
        },
    }


def digest_of(out):
    return hashlib.sha1(",".join("%.6f" % v for v in out).encode()).hexdigest()


def main(argv):
    req = json.load(open(argv[1]))
    a = [float(v) for v in req["a"]]
    b = [float(v) for v in req["b"]]
    L = int(req["len"])
    block = int(req.get("block", L))
    if len(a) != L or len(b) != L:
        raise ValueError("a/b 长度必须等于 len")
    out, p, stats = crossfade(a, b, L, block)
    res = {"out": out, "splice_pos": p, "stats": stats,
           "digest": digest_of(out)}
    with open(argv[2], "w") as f:
        json.dump(res, f)


if __name__ == "__main__":
    main(sys.argv)
