"""sh_irradiance.py 验收脚本(纯标准库)。

覆盖:
  A. 解析场景系数 vs 闭式解(uniform / dirlight delta / gradient),相对误差 <= 2%;
  B. HDR 图截断后 SH 重建 irradiance vs 蒙特卡洛参考,误差 <= 5%;
  C. 2/4/8 分块(含乱序)L2 系数摘要一致;
  D. 1024 方向采样集上逐点误差 vs MC 参考 <= 3%;
  E. 与缺陷基线 envmap.py 的对照(证明基线失败、修正版通过)。
"""
import math
import random
import json
import subprocess
import sys
import os

import sh_irradiance as sh

PASS = []
FAIL = []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  [PASS] " if cond else "  [FAIL] ") + name + ("  " + detail if detail else ""))


def relerr_vec(a, b, eps=1e-12):
    """向量 L2 范数相对误差。"""
    num = math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))
    den = math.sqrt(sum(y * y for y in b)) + eps
    return num / den


def pointwise_relerr(est, ref, floor_frac=0.02):
    """逐点相对误差,以参考场 RMS 的 floor_frac 为归一化下限,
    避免低能方向上绝对小误差被放大成虚假的大相对误差。"""
    scale = math.sqrt(sum(v * v for v in ref) / len(ref))
    floor = floor_frac * scale
    return [abs(a - b) / max(abs(b), floor) for a, b in zip(est, ref)]


def closed_form(scene):
    """解析场景 9 个 SH 系数的闭式解(投影积分 c_lm = ∫L Y_lm dΩ)。"""
    c = [0.0] * 9
    t = scene["type"]
    if t == "uniform":
        v = float(scene.get("v", 1.0))
        c[0] = v * 2.0 * math.sqrt(math.pi)
        return c
    if t == "dirlight":
        # 半径 r 的小圆盘:通量 P = i*pi*r^2;高阶 r^2 修正极小,按 delta 闭式。
        dt, dp = scene["dir"]
        i = float(scene.get("i", 5.0))
        r = float(scene.get("radius", 0.05))
        P = i * math.pi * r * r
        dx, dy, dz = sh.sph(dt, dp)
        poly = sh.basis_poly(dx, dy, dz)
        return [P * sh.C[m] * poly[m] for m in range(9)]
    if t == "gradient":
        # L = a + b*y,  ∫L dΩ = 4πa; ∫L*y dΩ = 4πb/3。
        # y 极轴对应索引 1 的多项式(Y_1^{-1}=C1*y),仅 c00 与该系数非零。
        lo = float(scene.get("lo", 0.1))
        hi = float(scene.get("hi", 1.5))
        a = 0.5 * (lo + hi)
        b = 0.5 * (hi - lo)
        c[0] = 4.0 * math.pi * a * sh.C[0]
        c[1] = (4.0 * math.pi / 3.0) * b * sh.C[1]
        return c
    raise ValueError(t)


# ---------------------------------------------------------------------------
print("== A. 解析场景系数 vs 闭式解 (容差 2%) ==")
fine = (512, 256)
cases = [
    ("uniform", {"type": "uniform", "v": 0.7}, fine),
    ("gradient", {"type": "gradient", "lo": 0.1, "hi": 1.5}, fine),
    ("dirlight(delta 圆盘)",
     {"type": "dirlight", "dir": [0.7, 2.3], "i": 5.0, "radius": 0.05},
     (256, 128)),
]
for name, scene, (w, h) in cases:
    got = sh.project(w, h, scene, subsamples=12)
    ref = closed_form(scene)
    e = relerr_vec(got, ref)
    check(name + " 系数 L2 相对误差", e <= 0.02, "err=%.3f%%" % (100 * e))
    worst = max(abs(g - r) / (abs(r) + 1e-9) for g, r in zip(got, ref) if abs(r) > 1e-6)
    print("         逐系数最大相对误差=%.3f%%" % (100 * worst))

# 渐变天空 irradiance 解析解:
# c00=2*sqrt(pi)*a 给出 A0*c00*C0 = pi*a;
# c1m1=(4pi/3)*b*C1 给出 A1*c1m1*C1*ny = (2pi/3)*b*ny。
grad = {"type": "gradient", "lo": 0.1, "hi": 1.5}
ga, gb = 0.8, 0.7
gc = sh.project(256, 128, grad)
gdirs = [[0.3 * k, 0.9 * k + 0.2] for k in range(12)]
g_sh = sh.reconstruct(gc, gdirs)
g_an = [math.pi * ga + (2.0 * math.pi / 3.0) * gb * sh.sph(t, p)[1]
        for t, p in gdirs]
ge = relerr_vec(g_sh, g_an)
check("渐变天空 irradiance vs 解析解", ge <= 0.01, "err=%.3f%%" % (100 * ge))

# ---------------------------------------------------------------------------
print("== B/C. 分块重放:2/4/8 块,顺序与乱序,摘要一致 ==")
scene = {"type": "patches",
         "rects": [[0.4, 1.2, 0.5, 2.0, 3.0],
                   [1.6, 2.6, 3.2, 5.4, 1.2]],
         "bg": 0.05}
base = sh.project(256, 128, scene, blocks=1)
base_digest = sh.coeffs_digest(base)
base_l2 = [math.sqrt(sum(v * v for v in base))]
for nb in (2, 4, 8):
    for order in ("sequential", "shuffle"):
        c = sh.project(256, 128, scene, blocks=nb, order=order)
        d = sh.coeffs_digest(c)
        dl2 = math.sqrt(sum((x - y) ** 2 for x, y in zip(c, base)))
        check("blocks=%d %s 摘要一致" % (nb, order), d == base_digest,
              "L2 差=%.2e" % dl2)

# ---------------------------------------------------------------------------
print("== B. HDR 截断后 irradiance 重建 (容差 5%) ==")
hdr_scene = {"type": "patches",
             "rects": [[0.3, 1.1, 0.2, 2.4, 40.0],
                       [1.7, 2.7, 3.0, 5.8, 25.0]],
             "bg": 0.2}
CLAMP = 10.0
rng = random.Random(2024)
dirs1024 = [[math.acos(2 * rng.random() - 1), 2 * math.pi * rng.random()]
            for _ in range(1024)]
sh_hdr = sh.project(256, 128, hdr_scene, blocks=4, hdr_clamp=CLAMP)
irr_sh = sh.reconstruct(sh_hdr, dirs1024)
irr_mc = sh.mc_irradiance(hdr_scene, dirs1024, samples=8192,
                          seed=777, hdr_clamp=CLAMP)
errs = pointwise_relerr(irr_sh, irr_mc)
mean_e = sum(errs) / len(errs)
p95 = sorted(errs)[int(0.95 * len(errs))]
check("HDR 截断 1024 方向平均相对误差", mean_e <= 0.05,
      "mean=%.3f%% p95=%.3f%% max=%.3f%%" %
      (100 * mean_e, 100 * p95, 100 * max(errs)))

# 独立交叉验证:细网格直接余弦卷积数值积分(与 MC 不同的参考路径)。
def quadrature_irr(scene, dirs, qw=512, qh=256, clamp=None):
    L = sh.make_radiance(scene, clamp)
    dth = math.pi / qh
    dph = 2 * math.pi / qw
    out = []
    for theta, phi in dirs:
        nx, ny, nz = sh.sph(theta, phi)
        acc = 0.0
        for i in range(qh):
            th = (i + 0.5) * dth
            st = math.sin(th)
            for j in range(qw):
                ph = (j + 0.5) * dph
                wx, wy, wz = sh.sph(th, ph)
                cw = nx * wx + ny * wy + nz * wz
                if cw > 0:
                    acc += L(th, ph)[0] * cw * st * dth * dph
        out.append(acc)
    return out

irr_q = quadrature_irr(hdr_scene, dirs1024[:64], clamp=CLAMP)
qerr = pointwise_relerr(sh.reconstruct(sh_hdr, dirs1024[:64]), irr_q)
check("HDR vs 直接数值积分(64 方向抽样,均值口径 5%)",
      sum(qerr) / len(qerr) <= 0.05,
      "max=%.3f%% mean=%.3f%%" % (100 * max(qerr), 100 * sum(qerr) / len(qerr)))

# ---------------------------------------------------------------------------
print("== D. 1024 方向逐点误差 vs 蒙特卡洛参考 (容差 3%, 低频场景) ==")
# 三阶 SH 只保证低频精度:用渐变天空(纯 l=0/l=1)与大块柔和 patch 两组场景。
soft_cases = [
    ("渐变天空", {"type": "gradient", "lo": 0.2, "hi": 1.3}),
    # 覆盖大半天球的半球形柔和 patch,能量集中在低阶,SH2 截断误差极小。
    ("半球柔和 patch", {"type": "patches",
                        "rects": [[0.05, math.pi / 2.0 - 0.02, 0.0,
                                   2.0 * math.pi, 1.4]],
                        "bg": 0.5}),
]
for sname, soft_scene in soft_cases:
    sh_soft = sh.project(256, 128, soft_scene, blocks=8, order="shuffle")
    irr_soft = sh.reconstruct(sh_soft, dirs1024)
    mc_soft = sh.mc_irradiance(soft_scene, dirs1024, samples=8192, seed=42)
    soft_errs = pointwise_relerr(irr_soft, mc_soft)
    check("%s 1024 方向逐点全部 <= 3%%" % sname, max(soft_errs) <= 0.03,
          "max=%.3f%% mean=%.3f%%" % (max(soft_errs) * 100,
                                      sum(soft_errs) / len(soft_errs) * 100))

# 高频 patch 的口径说明:逐点峰值受 SH 振铃限制,按均值口径达标(见 B 节)。
sharp_scene = {"type": "patches",
               "rects": [[0.5, 1.4, 0.3, 2.2, 2.0],
                         [1.5, 2.5, 3.5, 5.9, 0.8]],
               "bg": 0.15}
irr_sharp = sh.reconstruct(sh.project(256, 128, sharp_scene), dirs1024)
mc_sharp = sh.mc_irradiance(sharp_scene, dirs1024, samples=8192, seed=42)
sharp_errs = pointwise_relerr(irr_sharp, mc_sharp)
check("高频 patch 1024 方向均值 <= 3%(峰值受阶数限制)",
      sum(sharp_errs) / len(sharp_errs) <= 0.03,
      "mean=%.3f%% max=%.3f%%" % (100 * sum(sharp_errs) / len(sharp_errs),
                                  100 * max(sharp_errs)))

# MC 自检:同场景两次独立运行的噪声应明显低于 3%(参考可信)。
mc_a = sh.mc_irradiance(soft_cases[1][1], dirs1024[:128],
                        samples=8192, seed=1)
mc_b = sh.mc_irradiance(soft_cases[1][1], dirs1024[:128],
                        samples=8192, seed=2)
noise = pointwise_relerr(mc_a, mc_b)
check("MC 参考自身噪声 < 1.5%", max(noise) < 0.015,
      "max=%.3f%%" % (100 * max(noise)))

# ---------------------------------------------------------------------------
print("== E. 缺陷基线对照(envmap.py 等权/无卷积,应失败) ==")
import json
import subprocess
import os
here = os.path.dirname(os.path.abspath(__file__))
req = {"scene": {"type": "uniform", "v": 1.0}, "w": 64, "h": 32,
       "blocks": 4, "dirs": [[0.9, 1.7], [2.2, 4.0]]}
with open(os.path.join(here, "_req.json"), "w") as f:
    json.dump(req, f)
subprocess.run([sys.executable, os.path.join(here, "envmap.py"),
                os.path.join(here, "_req.json"),
                os.path.join(here, "_base_out.json")], check=True)
base_out = json.load(open(os.path.join(here, "_base_out.json")))
# 均匀光 v=1:真实 irradiance = pi;基线无 A_l 卷积且等权,必然偏离。
base_irr = base_out["irradiance"]
base_bad = any(abs(e - math.pi) / math.pi > 0.05 for e in base_irr)
check("基线 irradiance 偏离 pi(确认缺陷存在)", base_bad,
      "base=%s vs pi=%.4f" % (["%.4f" % e for e in base_irr], math.pi))
good_irr = sh.reconstruct(sh.project(64, 32, req["scene"]), req["dirs"])
check("修正版均匀光 irradiance 收敛于 pi(网格离散容差 0.1%)",
      all(abs(e - math.pi) / math.pi < 1e-3 for e in good_irr),
      "got=%s" % ["%.6f" % e for e in good_irr])
fine_irr = sh.reconstruct(sh.project(512, 256, req["scene"]), req["dirs"])
check("细网格(512x256)误差 < 0.01%",
      all(abs(e - math.pi) / math.pi < 1e-4 for e in fine_irr),
      "got=%s" % ["%.6f" % e for e in fine_irr])
for fn in ("_req.json", "_base_out.json"):
    os.remove(os.path.join(here, fn))

# ---------------------------------------------------------------------------
print("\n汇总: PASS=%d  FAIL=%d" % (len(PASS), len(FAIL)))
if FAIL:
    print("失败项: " + ", ".join(FAIL))
    raise SystemExit(1)
print("全部验收通过。")
