"""跨面无接缝立方体环境贴图采样与粗糙度预过滤。

约定(与 README 一致):
  面顺序 +x,-x,+y,-y,+z,-z;行号对应 v、列号对应 u;
  第 i 个像素中心为 2(i+0.5)/N-1;direction/locate 定义统一的六面坐标
  与正负轴取向,主轴绝对值相同按 x、y、z 优先;输入方向尺度不影响结果。

关键性质:
  * sample 为跨面双线性过滤:中心面某邻点越过边或角时,把该邻点中心
    转换成三维向量,在落入的面取对应最近像素,不在原面夹取,因此沿所有
    边和角的两侧采样结果连续(同一物理方向唯一映射)。
  * 预过滤按源像素覆盖的精确立体角加权(矩形对原点的 atan 精确公式,
    误差仅浮点舍入),核 max(0,dot(d,n))^k,k=max(0,2/r^2-2),k=0 时核
    为正半球常数 1;累加后按权重归一化,常量环境严格保持常量,亮斑能量
    受核积分约束。核截断阈值 c^k 小于 e^-18,引入相对误差约 1.5e-8。
  * 粗糙度级别由等长列表 roughness[i]/sizes[i] 显式给出(粗糙度到
    贴图层的映射);r=0 的层用跨面双线性把基图重采样到该层 size。
    积分为确定性求和,结果可复现、与 seed 无关(seed 被接受以兼容输入)。
  所有计算和输出均为线性亮度。
"""
import argparse
import json
import math
from pathlib import Path

FACES = ("+x", "-x", "+y", "-y", "+z", "-z")


def direction(face, u, v):
    """面局部坐标 (u,v) -> 未归一化三维方向(统一六面约定)。"""
    return {
        "+x": (1, -v, -u), "-x": (-1, -v, u), "+y": (u, 1, v),
        "-y": (u, -1, -v), "+z": (u, -v, 1), "-z": (-u, -v, -1),
    }[face]


def locate(d):
    """三维方向 -> (face,u,v);主轴相同按 x、y、z 优先;与 |d| 无关。"""
    x, y, z = d
    if abs(x) >= max(abs(y), abs(z)):
        return ("+x", -z / x, -y / x) if x > 0 else ("-x", z / -x, y / x)
    if abs(y) >= abs(z):
        return ("+y", x / y, z / y) if y > 0 else ("-y", x / -y, z / y)
    return ("+z", x / z, -y / z) if z > 0 else ("-z", x / z, y / z)


def _fetch(faces, face, n, i, j):
    """取像素 (i,j);越界时把邻点中心换成三维方向,在落入面取最近像素。"""
    if 0 <= i < n and 0 <= j < n:
        return faces[face][j][i]
    d = direction(face, 2.0 * (i + 0.5) / n - 1.0, 2.0 * (j + 0.5) / n - 1.0)
    face2, u2, v2 = locate(d)
    grid = faces[face2]
    n2 = len(grid)
    i2 = min(n2 - 1, max(0, int((u2 + 1.0) * 0.5 * n2)))
    j2 = min(n2 - 1, max(0, int((v2 + 1.0) * 0.5 * n2)))
    return grid[j2][i2]


def sample(faces, d):
    """方向 d 的跨面双线性采样(线性亮度)。"""
    face, u, v = locate(d)
    n = len(faces[face])
    x = (u + 1.0) * 0.5 * n - 0.5
    y = (v + 1.0) * 0.5 * n - 0.5
    i0 = math.floor(x)
    j0 = math.floor(y)
    fx = x - i0
    fy = y - j0
    acc = 0.0
    for dj in (0, 1):
        wy = fy if dj else 1.0 - fy
        for di in (0, 1):
            wx = fx if di else 1.0 - fx
            acc += wx * wy * _fetch(faces, face, n, i0 + di, j0 + dj)
    return acc


def _rect_solid_angle(u0, u1, v0, v1):
    # 平面矩形对原点的精确立体角:
    # 被积函数 (1+u^2+v^2)^(-3/2) 的二重积分,原函数 atan(u v / sqrt(1+u^2+v^2))。
    # 六面的 (e_u,e_v,法线) 均为标准正交基,该公式逐面适用。
    def f(u, v):
        return math.atan2(u * v, math.sqrt(1.0 + u * u + v * v))
    return f(u1, v1) - f(u0, v1) - f(u1, v0) + f(u0, v0)


def texel_solid_angle(n, i, j):
    """第 (i,j) 像素覆盖的精确立体角(任意面相同,仅依赖分辨率)。"""
    u0 = 2.0 * i / n - 1.0
    u1 = 2.0 * (i + 1) / n - 1.0
    v0 = 2.0 * j / n - 1.0
    v1 = 2.0 * (j + 1) / n - 1.0
    return _rect_solid_angle(u0, u1, v0, v1)


def kernel_exponent(roughness):
    """核 max(0,dot(d,n))^k 的指数:k=max(0, 2/r^2 - 2)。"""
    return max(0.0, 2.0 / (roughness * roughness) - 2.0)


def _source_texels(faces):
    n = len(faces["+x"])
    dirs, weights, values = [], [], []
    for f in FACES:
        grid = faces[f]
        if len(grid) != n or any(len(row) != n for row in grid):
            raise ValueError("六面必须为同分辨率方阵")
        for j in range(n):
            v = 2.0 * (j + 0.5) / n - 1.0
            for i in range(n):
                u = 2.0 * (i + 0.5) / n - 1.0
                dx, dy, dz = direction(f, u, v)
                inv = 1.0 / math.sqrt(dx * dx + dy * dy + dz * dz)
                dirs.append((dx * inv, dy * inv, dz * inv))
                weights.append(texel_solid_angle(n, i, j))
                values.append(grid[j][i])
    return dirs, weights, values


def prefilter_level(faces, size, roughness):
    """按归一化核与源像素立体角权重,生成 size 级六面预过滤图。"""
    dirs, weights, values = _source_texels(faces)
    k = kernel_exponent(roughness)
    # 核截断:c^k 小于 e^-18 (约 1.5e-8) 的源像素对归一化结果相对影响可忽略。
    c_cut = 0.0 if k == 0.0 else math.exp(-18.0 / k)
    out = {}
    for f in FACES:
        grid = []
        for j in range(size):
            v = 2.0 * (j + 0.5) / size - 1.0
            row = []
            for i in range(size):
                u = 2.0 * (i + 0.5) / size - 1.0
                nx, ny, nz = direction(f, u, v)
                inv = 1.0 / math.sqrt(nx * nx + ny * ny + nz * nz)
                nx, ny, nz = nx * inv, ny * inv, nz * inv
                num = 0.0
                den = 0.0
                for (dx, dy, dz), w, val in zip(dirs, weights, values):
                    c = dx * nx + dy * ny + dz * nz
                    if c <= c_cut:
                        continue
                    ww = w if k == 0.0 else w * c ** k
                    num += ww * val
                    den += ww
                # den 恒正(半球内必有源像素);防御性回退为基图采样。
                row.append(num / den if den > 0.0 else sample(faces, (nx, ny, nz)))
            grid.append(row)
        out[f] = grid
    return out


def resample_level(faces, size):
    """r=0 的层:以跨面双线性把基图重采样到该层 size。"""
    out = {}
    for f in FACES:
        grid = []
        for j in range(size):
            v = 2.0 * (j + 0.5) / size - 1.0
            row = []
            for i in range(size):
                u = 2.0 * (i + 0.5) / size - 1.0
                row.append(sample(faces, direction(f, u, v)))
            grid.append(row)
        out[f] = grid
    return out


def run(data):
    faces = data["faces"]
    directions = data.get("directions", [])
    roughness = data.get("roughness", [])
    sizes = data.get("sizes", [])
    if len(roughness) != len(sizes):
        raise ValueError("roughness 与 sizes 必须为等长列表")
    levels = []
    for r, s in zip(roughness, sizes):
        r = float(r)
        s = int(s)
        if s < 1:
            raise ValueError("size 必须为正整数")
        # 显式粗糙度 -> 贴图层映射:levels[i] 对应 (roughness[i], sizes[i])
        level_faces = resample_level(faces, s) if r <= 0.0 else prefilter_level(faces, s, r)
        levels.append({
            "roughness": r,
            "size": s,
            "samples": [sample(level_faces, d) for d in directions],
            "faces": level_faces,
        })
    return {
        "samples": [sample(faces, d) for d in directions],
        "levels": levels,
    }


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    data=json.loads(args.input.read_text())
    args.output.write_text(json.dumps(run(data),sort_keys=True,allow_nan=False)+"\n")


if __name__=="__main__":
    main()
