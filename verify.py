"""验收脚本(纯 Python,无第三方依赖):python3 verify.py

验收 1:沿所有立方体边和角的两侧采样,与独立球面方向参考(解析环境按
  球坐标方向求值)比较。常量环境任意方向线性色误差 <= 1e-5;连续解析
  环境的跨面误差不高于同分辨率独立方向采样容差(取 3 倍裕度)。
验收 2:旋转环境在不同粗糙度下的预过滤结果,与核 max(0,dot)^k 的高样本
  球面积分(Fibonacci 球面 120k 样本)对照,预先指定方向的归一化积分
  均值误差 <= 0.03。
附加:精确立体角总和为 4*pi;输入方向尺度不变性;固定种子可复现。
"""
import math
import random
import sys

import cube


def normalize(d):
    x, y, z = d
    l = math.sqrt(x * x + y * y + z * z)
    return (x / l, y / l, z / l)


def dir_spherical(theta, phi):
    st = math.sin(theta)
    return (st * math.cos(phi), st * math.sin(phi), math.cos(theta))


def env_analytic(d):
    """连续解析环境(独立球面方向参考),正值、平滑。"""
    x, y, z = normalize(d)
    return (0.55 + 0.22 * x - 0.13 * y
            + 0.09 * math.sin(2.0 * y + 0.4) * math.cos(2.0 * z - 0.2)
            + 0.05 * math.cos(3.0 * (x + z)))


def make_faces(n, func):
    faces = {}
    for f in cube.FACES:
        grid = []
        for j in range(n):
            v = 2.0 * (j + 0.5) / n - 1.0
            row = []
            for i in range(n):
                u = 2.0 * (i + 0.5) / n - 1.0
                row.append(func(cube.direction(f, u, v)))
            grid.append(row)
        faces[f] = grid
    return faces


def edge_side_pairs(eps, t):
    """12 条边:每条边给定参数 t,返回 (侧A方向, 侧B方向, 边上精确方向)。"""
    triples = []
    axes = [(0, 1, 2), (0, 2, 1), (1, 2, 0)]
    for a, b, c in axes:
        for sa in (1.0, -1.0):
            for sb in (1.0, -1.0):
                exact = [0.0, 0.0, 0.0]
                exact[a], exact[b], exact[c] = sa, sb, t
                side_a = list(exact)
                side_a[b] = sb * (1.0 - eps)   # 主轴落在 a 侧
                side_b = list(exact)
                side_b[a] = sa * (1.0 - eps)   # 主轴落在 b 侧
                triples.append((tuple(side_a), tuple(side_b), tuple(exact)))
    return triples


def corner_side_dirs(eps):
    """8 个角:每个角返回 (三个面侧方向列表, 角上精确方向)。"""
    out = []
    for sx in (1.0, -1.0):
        for sy in (1.0, -1.0):
            for sz in (1.0, -1.0):
                exact = (sx, sy, sz)
                sides = []
                for axis in range(3):
                    d = [sx, sy, sz]
                    d[axis] *= (1.0 - eps)
                    sides.append(tuple(d))
                out.append((sides, exact))
    return out


def test_constant():
    n = 8
    value = 0.4
    faces = {f: [[value] * n for _ in range(n)] for f in cube.FACES}
    rng = random.Random(20260709)
    dirs = [dir_spherical(math.acos(rng.uniform(-1, 1)), rng.uniform(0, 2 * math.pi))
            for _ in range(4000)]
    for _, _, exact in edge_side_pairs(1e-6, 0.0):
        dirs.append(exact)
    for _, exact in corner_side_dirs(1e-6):
        dirs.append(exact)
    worst = max(abs(cube.sample(faces, d) - value) for d in dirs)
    # 预过滤各级同样保持常量
    data = {"faces": faces, "directions": dirs[:64],
            "roughness": [0.0, 0.25, 0.75, 1.0], "sizes": [8, 8, 4, 2], "seed": 7}
    result = cube.run(data)
    worst_lvl = 0.0
    for lvl in result["levels"]:
        for f in cube.FACES:
            for row in lvl["faces"][f]:
                for x in row:
                    worst_lvl = max(worst_lvl, abs(x - value))
        for x in lvl["samples"]:
            worst_lvl = max(worst_lvl, abs(x - value))
    print(f"[1a] 常量环境: 双线性最大误差 {worst:.3e}, 各级预过滤最大误差 {worst_lvl:.3e}")
    assert worst <= 1e-5 and worst_lvl <= 1e-5, "常量环境误差超过 1e-5"


def test_edges_corners():
    n = 16
    faces = make_faces(n, env_analytic)
    rng = random.Random(99)
    # 同分辨率独立方向采样容差:面内部随机方向相对解析参考的最大误差
    tol = 0.0
    for _ in range(4000):
        d = dir_spherical(math.acos(rng.uniform(-1, 1)), rng.uniform(0, 2 * math.pi))
        tol = max(tol, abs(cube.sample(faces, d) - env_analytic(d)))
    eps = 1e-4
    worst_jump = worst_edge = 0.0
    for t in (-0.9, -0.35, 0.2, 0.85):
        for side_a, side_b, exact in edge_side_pairs(eps, t):
            va, vb = cube.sample(faces, side_a), cube.sample(faces, side_b)
            ref = env_analytic(exact)
            worst_jump = max(worst_jump, abs(va - vb))
            worst_edge = max(worst_edge, abs(va - ref), abs(vb - ref))
    worst_corner = 0.0
    for sides, exact in corner_side_dirs(eps):
        vals = [cube.sample(faces, d) for d in sides]
        ref = env_analytic(exact)
        for i in range(3):
            for j in range(i + 1, 3):
                worst_corner = max(worst_corner, abs(vals[i] - vals[j]))
        worst_corner = max(worst_corner, *(abs(v - ref) for v in vals))
    print(f"[1b] 连续解析环境: 内部容差 {tol:.3e}; 边两侧跳变 {worst_jump:.3e}, "
          f"边误差 {worst_edge:.3e}, 角误差 {worst_corner:.3e}")
    limit = 3.0 * tol
    assert worst_jump <= limit and worst_edge <= limit and worst_corner <= limit, \
        "跨面误差高于同分辨率独立方向采样容差"


def test_solid_angle_and_scale():
    for n in (1, 2, 4, 8, 16):
        total = sum(cube.texel_solid_angle(n, i, j)
                    for j in range(n) for i in range(n))
        err = abs(6.0 * total - 4.0 * math.pi)
        assert err < 1e-12, f"立体角总和误差 {err}"
    faces = make_faces(8, env_analytic)
    rng = random.Random(5)
    worst = 0.0
    for _ in range(500):
        d = dir_spherical(math.acos(rng.uniform(-1, 1)), rng.uniform(0, 2 * math.pi))
        for s in (1e-3, 7.0, 1e4):
            worst = max(worst, abs(cube.sample(faces, d)
                                   - cube.sample(faces, tuple(s * c for c in d))))
    print(f"[1c] 六面立体角总和 = 4*pi (误差<1e-12); 方向尺度不变性最大偏差 {worst:.3e}")
    assert worst <= 1e-12


def rotate(d, axis, angle):
    x, y, z = d
    ax, ay, az = normalize(axis)
    c, s = math.cos(angle), math.sin(angle)
    dot = ax * x + ay * y + az * z
    return (x * c + (ay * z - az * y) * s + ax * dot * (1 - c),
            y * c + (az * x - ax * z) * s + ay * dot * (1 - c),
            z * c + (ax * y - ay * x) * s + az * dot * (1 - c))


AXIS = normalize((1.0, 2.0, 3.0))
ANGLE = 0.7


def env_rotated(d):
    return env_analytic(rotate(d, AXIS, -ANGLE))


def fibonacci_sphere(m):
    pts = []
    ga = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(m):
        z = 1.0 - 2.0 * (i + 0.5) / m
        r = math.sqrt(max(0.0, 1.0 - z * z))
        a = ga * i
        pts.append((r * math.cos(a), r * math.sin(a), z))
    return pts


def reference_integral(env, pts, n_dir, k):
    nx, ny, nz = normalize(n_dir)
    num = den = 0.0
    for dx, dy, dz in pts:
        c = dx * nx + dy * ny + dz * nz
        if c <= 0.0:
            continue
        w = 1.0 if k == 0.0 else c ** k
        num += w * env((dx, dy, dz))
        den += w
    return num / den


def test_prefilter_vs_reference():
    n_src = 32
    faces = make_faces(n_src, env_rotated)
    roughness = [0.25, 0.5, 1.0]
    sizes = [16, 8, 8]
    levels = [cube.prefilter_level(faces, s, r) for r, s in zip(roughness, sizes)]
    # 预先指定的对照方向
    test_dirs = [
        (1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1),
        (1, 1, 1), (-1, 1, -1), (1, -2, 3), (-3, -1, 2), (2, 3, -1), (-2, 4, 1),
    ]
    pts = fibonacci_sphere(120000)
    print("[2] 旋转环境预过滤 vs 120k 样本球面积分:")
    all_err = []
    for r, lvl in zip(roughness, levels):
        k = cube.kernel_exponent(r)
        errs = []
        for d in test_dirs:
            got = cube.sample(lvl, d)
            ref = reference_integral(env_rotated, pts, d, k)
            errs.append(abs(got - ref))
        mean_err = sum(errs) / len(errs)
        all_err.extend(errs)
        print(f"    r={r:.2f} (k={cube.kernel_exponent(r):8.3f}): "
              f"均值误差 {mean_err:.4f}, 最大 {max(errs):.4f}")
        assert mean_err <= 0.03, f"r={r} 归一化积分均值误差 {mean_err} 超过 0.03"
    overall = sum(all_err) / len(all_err)
    print(f"    全部方向与粗糙度的总均值误差 {overall:.4f} (要求 <= 0.03)")
    assert overall <= 0.03


def test_reproducibility():
    faces = make_faces(8, env_rotated)
    data = {"faces": faces, "directions": [(1, 2, 3), (-1, 0.5, 2)],
            "roughness": [0.3, 0.8], "sizes": [8, 4], "seed": 42}
    a = cube.run(data)
    b = cube.run(data)
    data2 = dict(data, seed=1234)  # 确定性积分:不同 seed 结果一致
    c = cube.run(data2)
    assert a == b == c, "固定条件下结果不可复现"
    print("[3] 固定种子可复现:两次运行及不同 seed 的确定性积分结果完全一致")


def main():
    test_constant()
    test_edges_corners()
    test_solid_angle_and_scale()
    test_prefilter_vs_reference()
    test_reproducibility()
    print("全部验收通过")


if __name__ == "__main__":
    sys.exit(main())
