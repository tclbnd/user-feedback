"""三阶球谐漫反射 IBL:环境图 -> SH(9 系数/通道) -> 余弦卷积 irradiance。

相对缺陷基线 envmap.py 的修正:
  1. 投影按纹素立体角 dOmega = sin(theta)*dtheta*dphi 加权(正确处理两极拉伸);
  2. irradiance 重建做 SH 余弦卷积,逐带 A_l 缩放(A0=pi, A1=2pi/3, A2=pi/4);
  3. 投影按 blocks 分块,块内普通求和、块间 math.fsum 归约(可乱序重放);
  4. 方向光 delta(小圆盘)在边界纹素做子采样,抑制栅格化面积误差。

CLI:  python3 sh_irradiance.py input.json output.json
输入/输出契约与 envmap.py 兼容,输出额外携带 stats / per_dir 能量统计。
"""
import json
import sys
import math
import hashlib
import random

# ----------------------------------------------------------------------------
# 三阶实球谐基(Condon-Shortley 约定),与基线相同的索引顺序:
#  0: Y_0^0   1..3: Y_1^{-1..1}   4..8: Y_2^{-2..2}
# C 为归一化常数,basis_poly 只含多项式部分;两者相乘为完整 SH 基函数。
# ----------------------------------------------------------------------------
SQRT_PI = math.sqrt(math.pi)
C = [
    1.0 / (2.0 * SQRT_PI),                 # 0  Y00
    math.sqrt(3.0) / (2.0 * SQRT_PI),      # 1  Y1-1
    math.sqrt(3.0) / (2.0 * SQRT_PI),      # 2  Y1 0
    math.sqrt(3.0) / (2.0 * SQRT_PI),      # 3  Y1 1
    math.sqrt(15.0) / (2.0 * SQRT_PI),     # 4  Y2-2
    math.sqrt(15.0) / (2.0 * SQRT_PI),     # 5  Y2-1
    math.sqrt(5.0) / (4.0 * SQRT_PI),      # 6  Y2 0
    math.sqrt(15.0) / (2.0 * SQRT_PI),     # 7  Y2 1
    math.sqrt(15.0) / (4.0 * SQRT_PI),     # 8  Y2 2
]
BAND = (0, 1, 1, 1, 2, 2, 2, 2, 2)
# 余弦卷积核的 SH 带缩放 A_l(漫反射 irradiance)。
A_L = {0: math.pi, 1: 2.0 * math.pi / 3.0, 2: math.pi / 4.0}


def basis_poly(x, y, z):
    """9 个 SH 多项式部分(乘以对应 C[m] 后即完整基函数)。"""
    return (
        1.0,
        y, z, x,
        x * y, y * z, 3.0 * z * z - 1.0, x * z, x * x - y * y,
    )


def sph(theta, phi):
    """球坐标 -> 单位方向 (x,y,z),y 为极轴(theta=0 指向 +y)。"""
    st = math.sin(theta)
    return st * math.cos(phi), math.cos(theta), st * math.sin(phi)


def as_rgb(v):
    """标量场景走单通道;RGB 场景统一为 3 列表。"""
    if isinstance(v, (list, tuple)):
        return [float(v[0]), float(v[1]), float(v[2])]
    return [float(v)]


def make_radiance(scene, hdr_clamp=None):
    """构造解析环境 L(theta,phi),返回通道列表(1 或 3 通道)。"""
    t = scene["type"]

    if t == "uniform":
        v = float(scene.get("v", 1.0))

        def L(theta, phi):
            return [v]

    elif t == "dirlight":
        dt, dp = scene["dir"]
        dx, dy, dz = sph(dt, dp)
        cos_r = math.cos(float(scene.get("radius", 0.05)))
        i = as_rgb(scene.get("i", 5.0))
        nch = len(i)

        def L(theta, phi):
            x, y, z = sph(theta, phi)
            if x * dx + y * dy + z * dz > cos_r:
                return list(i)
            return [0.0] * nch

    elif t == "gradient":
        lo = float(scene.get("lo", 0.1))
        hi = float(scene.get("hi", 1.5))

        def L(theta, phi):
            return [lo + (hi - lo) * (0.5 + 0.5 * math.cos(theta))]

    elif t == "patches":
        rects = scene["rects"]
        bg = as_rgb(scene.get("bg", 0.02))
        nch = len(bg)

        def L(theta, phi):
            for t0, t1, p0, p1, val in rects:
                if t0 <= theta < t1 and p0 <= phi < p1:
                    if isinstance(val, (list, tuple)):
                        return as_rgb(val)
                    return [float(val)] * nch
            return list(bg)

    else:
        raise ValueError("unknown scene type: " + repr(t))

    if hdr_clamp is not None:
        cap = float(hdr_clamp)
        base = L

        def Lc(theta, phi):
            v = base(theta, phi)
            return [min(cap, k) for k in v]

        return Lc
    return L


# ----------------------------------------------------------------------------
# 纹素描述:中心方向、立体角权重、完整 SH 基值,以及方向光用的子采样标记。
# ----------------------------------------------------------------------------
def build_texels(w, h, scene, subsamples=12):
    dtheta = math.pi / h
    dphi = 2.0 * math.pi / w
    texels = []
    is_disk = scene["type"] == "dirlight"
    if is_disk:
        dt, dp = scene["dir"]
        dx, dy, dz = sph(dt, dp)
        r = float(scene.get("radius", 0.05))
        # 边界判定半径:圆盘角半径 + 纹素对角半角,保证覆盖所有跨边界纹素。
        cos_near = math.cos(r + math.hypot(dtheta, dphi))
        dvec = (dx, dy, dz)
    else:
        cos_near = None
        dvec = None
    for i in range(h):
        theta = (i + 0.5) * dtheta
        d_omega = math.sin(theta) * dtheta * dphi   # 纹素立体角(含 sin 极权重)
        for j in range(w):
            phi = (j + 0.5) * dphi
            x, y, z = sph(theta, phi)
            poly = basis_poly(x, y, z)
            sh = tuple(C[m] * poly[m] for m in range(9))
            ss = 1
            if is_disk and x * dvec[0] + y * dvec[1] + z * dvec[2] > cos_near:
                ss = int(subsamples)
            texels.append((i, j, theta, phi, d_omega, sh, ss))
    return texels


def project(w, h, scene, blocks=1, order="sequential", subsamples=12,
            hdr_clamp=None):
    """分块 SH 投影:c_lm = sum L * Y_lm * dOmega。

    blocks: 切分块数;order="shuffle" 时按确定性乱序重放块;
    块内普通求和、块间 math.fsum 归约,结果与块数/顺序无关(确定性)。
    """
    L = make_radiance(scene, hdr_clamp)
    texels = build_texels(w, h, scene, subsamples)
    nch = len(L(0.0, 0.0))
    n = len(texels)
    blocks = max(1, int(blocks))
    bounds = [k * n // blocks for k in range(blocks + 1)]
    block_order = list(range(blocks))
    if order == "shuffle":
        rng = random.Random(987654321)
        rng.shuffle(block_order)

    partials = {}
    dtheta = math.pi / h
    dphi = 2.0 * math.pi / w
    for b in block_order:
        acc = [[0.0] * 9 for _ in range(nch)]
        for idx in range(bounds[b], bounds[b + 1]):
            i, j, theta, phi, d_omega, sh, ss = texels[idx]
            if ss <= 1:
                val = L(theta, phi)
                for ch in range(nch):
                    v = val[ch] * d_omega
                    row = acc[ch]
                    for m in range(9):
                        row[m] += v * sh[m]
            else:
                # 边界纹素:子采样估计平均辐亮度,立体角权重仍只计一次。
                avg = [0.0] * nch
                for a in range(ss):
                    for b2 in range(ss):
                        ts = (i + (a + 0.5) / ss) * dtheta
                        ps = (j + (b2 + 0.5) / ss) * dphi
                        val = L(ts, ps)
                        for ch in range(nch):
                            avg[ch] += val[ch]
                inv = d_omega / (ss * ss)
                for ch in range(nch):
                    v = avg[ch] * inv
                    row = acc[ch]
                    for m in range(9):
                        row[m] += v * sh[m]
        partials[b] = acc

    coeffs = []
    for ch in range(nch):
        coeffs.append([math.fsum(partials[b][ch][m] for b in range(blocks))
                       for m in range(9)])
    return coeffs[0] if nch == 1 else coeffs


# ----------------------------------------------------------------------------
# irradiance 重建:E(n) = sum_lm c_lm * A_l * Y_lm(n)
# ----------------------------------------------------------------------------
def reconstruct(coeffs, dirs):
    multi = isinstance(coeffs[0], (list, tuple))
    out = []
    for theta, phi in dirs:
        x, y, z = sph(theta, phi)
        poly = basis_poly(x, y, z)
        if not multi:
            e = 0.0
            for m in range(9):
                e += coeffs[m] * A_L[BAND[m]] * C[m] * poly[m]
            out.append(e)
        else:
            e = [0.0] * len(coeffs)
            for ch, row in enumerate(coeffs):
                for m in range(9):
                    e[ch] += row[m] * A_L[BAND[m]] * C[m] * poly[m]
            out.append(e)
    return out


# ----------------------------------------------------------------------------
# 蒙特卡洛参考:以 n 为极轴的余弦加权半球采样。
# pdf(w) = cos(t')/pi;估计量 (1/N) sum L*cos/pdf = (pi/N) sum L。
# 分层抖动采样降低方差。
# ----------------------------------------------------------------------------
def mc_irradiance(scene, dirs, samples=4096, seed=12345, hdr_clamp=None):
    L = make_radiance(scene, hdr_clamp)
    rng = random.Random(seed)
    side = int(math.isqrt(samples))
    if side * side < samples:
        side += 1
    nch = len(L(0.0, 0.0))
    results = []
    for theta, phi in dirs:
        nx, ny, nz = sph(theta, phi)
        if abs(ny) < 0.999:
            tx, tz = -nz, nx                    # t = normalize(cross(y轴,n))
            tl = math.hypot(tx, tz)
            tx, tz = tx / tl, tz / tl
            ty = 0.0
        else:
            tx, ty, tz = 1.0, 0.0, 0.0
        # b = cross(n, t)
        bx = ny * tz - nz * ty
        by = nz * tx - nx * tz
        bz = nx * ty - ny * tx
        acc = [0.0] * nch
        used = 0
        for ia in range(side):
            for ib in range(side):
                if used >= samples:
                    break
                u1 = (ia + rng.random()) / side
                u2 = (ib + rng.random()) / side
                # 余弦加权半球 CDF:u1 = sin^2(t'),故
                # sin(t')=sqrt(u1) 为切向分量,cos(t')=sqrt(1-u1) 沿法线。
                sin_t = math.sqrt(u1)
                cos_t = math.sqrt(1.0 - u1)
                az = 2.0 * math.pi * u2
                lx = sin_t * math.cos(az)
                ly = cos_t
                lz = sin_t * math.sin(az)
                wx = lx * tx + ly * nx + lz * bx
                wy = lx * ty + ly * ny + lz * by
                wz = lx * tz + ly * nz + lz * bz
                wy = max(-1.0, min(1.0, wy))
                wtheta = math.acos(wy)
                wphi = math.atan2(wz, wx)
                if wphi < 0.0:
                    wphi += 2.0 * math.pi
                val = L(wtheta, wphi)
                for ch in range(nch):
                    acc[ch] += math.pi * val[ch]
                used += 1
        results.append([v / samples for v in acc])
    return [r[0] for r in results] if nch == 1 else results


def coeffs_digest(coeffs):
    """L2 系数摘要:固定 6 位小数序列化后 sha1,对累加顺序噪声不敏感。"""
    if isinstance(coeffs[0], (list, tuple)):
        flat = [v for row in coeffs for v in row]
    else:
        flat = list(coeffs)
    payload = ",".join("%.6f" % v for v in flat)
    return hashlib.sha1(payload.encode()).hexdigest()


def energy_stats(coeffs, irradiance):
    """逐带 L2 能量 + 逐方向 irradiance 统计。"""
    multi = isinstance(coeffs[0], (list, tuple))
    rows = coeffs if multi else [coeffs]
    bands = {}
    for l in range(3):
        vals = [math.sqrt(sum(row[m] * row[m] for m in range(9)
                              if BAND[m] == l)) for row in rows]
        bands["L%d_l2" % l] = vals if multi else vals[0]
    if multi:
        per_channel = list(zip(*irradiance))
        dstat = {
            "min": [min(c) for c in per_channel],
            "max": [max(c) for c in per_channel],
            "mean": [sum(c) / len(c) for c in per_channel],
        }
    else:
        dstat = {"min": min(irradiance), "max": max(irradiance),
                 "mean": sum(irradiance) / len(irradiance)}
    return {"band_l2": bands, "irradiance_stats": dstat,
            "directions": len(irradiance)}


def main():
    req = json.load(open(sys.argv[1]))
    w, h = int(req["w"]), int(req["h"])
    scene = req["scene"]
    blocks = int(req.get("blocks", 1))
    order = req.get("order", "sequential")
    subsamples = int(req.get("subsamples", 12))
    hdr_clamp = req.get("hdr_clamp")
    dirs = req.get("dirs", [])

    coeffs = project(w, h, scene, blocks=blocks, order=order,
                     subsamples=subsamples, hdr_clamp=hdr_clamp)
    irr = reconstruct(coeffs, dirs)
    res = {
        "coeffs": coeffs,
        "irradiance": irr,
        "block_count": blocks,
        "order": order,
        "digest": coeffs_digest(coeffs),
        "stats": energy_stats(coeffs, irr),
        "per_dir": [
            {"dir": list(d), "irradiance": e} for d, e in zip(dirs, irr)
        ],
    }
    with open(sys.argv[2], "w") as f:
        json.dump(res, f, indent=2)


if __name__ == "__main__":
    main()
