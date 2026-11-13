"""分块地形 LOD 回放：屏幕误差选层 + 滞回 + 粗细接缝缝合 + 单一覆盖来源。

用法: python3 terrain.py scene.json output.json

规则(见 README.md):
  level 0/1/2 -> 2/4/8 段。首帧(或中断后首次出现) error>=1.2 升一级, >=2.4 再升。
  之后从上一帧 level 起按升阈值 1.2/2.4、降阈值 0.8/1.8 滞回, 避免阈值附近抖动。
  ready=false 时回退到常驻祖先覆盖(level 0), 恢复后从 level 0 重新爬升。
  生成网格前先把相邻 tile 的 level 差约束到 <=1(只降低较细端, 迭代到不动点,
  结果与输入顺序无关)。
接缝: 细侧沿公共边缩并到粗侧采样点(两侧共享边使用完全同一组世界点);
  相邻两边同时缩并的角点用 2x2 角块过渡, 三角面保持闭合。
"""
import argparse
import json
from pathlib import Path

UP = (1.2, 2.4)      # 升阈值: 0->1, 1->2
DOWN = (0.8, 1.8)    # 降阈值: 1->0, 2->1
MAX_LEVEL = 2
SIDES = (("west", -1, 0), ("east", 1, 0), ("south", 0, -1), ("north", 0, 1))


def height(x, y):
    """解析地形: 斜坡 + x=1.35 处的尖脊。"""
    return 0.15 * x + 0.2 * y + 0.7 * max(0.0, 1.0 - abs(x - 1.35) * 2.0)


def _rise(error, lvl):
    while lvl < MAX_LEVEL and error >= UP[lvl]:
        lvl += 1
    return lvl


def _fall(error, lvl):
    while lvl > 0 and error < DOWN[lvl - 1]:
        lvl -= 1
    return lvl


def select_level(prev, error):
    """滞回状态机: prev 为上一帧生效 level(首次出现为 None)。"""
    if prev is None:
        return _rise(error, 0)
    if prev < MAX_LEVEL and error >= UP[prev]:
        return _rise(error, prev)
    if prev > 0 and error < DOWN[prev - 1]:
        return _fall(error, prev)
    return prev


def _orient(t):
    """把网格索引三角形统一成 CCW。"""
    a, b, c = t
    cross = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (a, c, b) if cross < 0 else t


# 2x2 角块(缩并角在局部 (0,0)): 边界只留 (0,0),(2,0),(0,2)
_CORNER = [((0, 0), (2, 0), (1, 1)), ((0, 0), (1, 1), (0, 2)),
           ((2, 0), (2, 1), (1, 1)), ((2, 1), (2, 2), (1, 1)),
           ((2, 2), (1, 2), (1, 1)), ((0, 2), (1, 1), (1, 2))]


def build_mesh(x0, y0, level, coarse_sides):
    """生成 unit 方块网格; coarse_sides 为邻居更粗、需要缩并的边集合。"""
    n = 2 ** (level + 1)
    s = 1.0 / n
    tris = []
    handled = set()
    W, E = "west" in coarse_sides, "east" in coarse_sides
    S, N = "south" in coarse_sides, "north" in coarse_sides

    # 角块: 两个相邻边同时缩并时, 先处理角上的 2x2 细胞组
    corners = []
    if S and W:
        corners.append((0, 0, lambda u, v: (u, v)))
    if S and E:
        corners.append((n - 2, 0, lambda u, v: (2 - u, v)))
    if N and W:
        corners.append((0, n - 2, lambda u, v: (u, 2 - v)))
    if N and E:
        corners.append((n - 2, n - 2, lambda u, v: (2 - u, 2 - v)))
    for ci, cj, f in corners:
        for t in _CORNER:
            tris.append(_orient(tuple((ci + f(*p)[0], cj + f(*p)[1]) for p in t)))
        for a in range(2):
            for b in range(2):
                handled.add((ci + a, cj + b))

    # 边块: 细侧边界丢弃奇数点, 每对细胞用 3 个三角形接到内部顶点
    if S:
        for i in range(0, n, 2):
            if (i, 0) in handled or (i + 1, 0) in handled:
                continue
            tris += [_orient(t) for t in (
                ((i, 0), (i + 2, 0), (i + 1, 1)),
                ((i, 0), (i + 1, 1), (i, 1)),
                ((i + 2, 0), (i + 2, 1), (i + 1, 1)))]
            handled.add((i, 0)); handled.add((i + 1, 0))
    if N:
        j = n - 1
        for i in range(0, n, 2):
            if (i, j) in handled or (i + 1, j) in handled:
                continue
            tris += [_orient(t) for t in (
                ((i, n), (i + 1, n - 1), (i + 2, n)),
                ((i, n), (i, n - 1), (i + 1, n - 1)),
                ((i + 2, n), (i + 1, n - 1), (i + 2, n - 1)))]
            handled.add((i, j)); handled.add((i + 1, j))
    if W:
        for j in range(0, n, 2):
            if (0, j) in handled or (0, j + 1) in handled:
                continue
            tris += [_orient(t) for t in (
                ((0, j), (0, j + 2), (1, j + 1)),
                ((0, j), (1, j + 1), (1, j)),
                ((0, j + 2), (1, j + 1), (1, j + 2)))]
            handled.add((0, j)); handled.add((0, j + 1))
    if E:
        for j in range(0, n, 2):
            if (n - 1, j) in handled or (n - 1, j + 1) in handled:
                continue
            tris += [_orient(t) for t in (
                ((n, j), (n, j + 2), (n - 1, j + 1)),
                ((n, j), (n - 1, j), (n - 1, j + 1)),
                ((n, j + 2), (n - 1, j + 1), (n - 1, j + 2)))]
            handled.add((n - 1, j)); handled.add((n - 1, j + 1))

    # 其余细胞常规剖分
    for j in range(n):
        for i in range(n):
            if (i, j) in handled:
                continue
            tris.append(((i, j), (i + 1, j), (i + 1, j + 1)))
            tris.append(((i, j), (i + 1, j + 1), (i, j + 1)))

    used = set()
    for t in tris:
        used.update(t)
    index, vertices = {}, []
    for j in range(n + 1):
        for i in range(n + 1):
            if (i, j) in used:
                index[(i, j)] = len(vertices)
                x, y = x0 + i * s, y0 + j * s
                vertices.append([x, y, height(x, y)])
    faces = [[index[a], index[b], index[c]] for a, b, c in tris]
    edges = {
        "west": [vertices[index[(0, j)]] for j in range(n + 1) if (0, j) in index],
        "east": [vertices[index[(n, j)]] for j in range(n + 1) if (n, j) in index],
        "south": [vertices[index[(i, 0)]] for i in range(n + 1) if (i, 0) in index],
        "north": [vertices[index[(i, n)]] for i in range(n + 1) if (i, n) in index],
    }
    return {"vertices": vertices, "faces": faces, "edges": edges}


def relax_levels(by_id, levels):
    """相邻 level 差 <=1: 只降低较细端, 迭代到不动点(与顺序无关的最大解)。"""
    pos = {(t["x"], t["y"]): t["id"] for t in by_id.values()}
    changed = True
    while changed:
        changed = False
        for tid in sorted(levels):
            t = by_id[tid]
            for _, dx, dy in SIDES:
                nb = pos.get((t["x"] + dx, t["y"] + dy))
                if nb is not None and levels[tid] > levels[nb] + 1:
                    levels[tid] = levels[nb] + 1
                    changed = True
    return levels


def replay(data):
    prev = {}  # tile id -> 上一帧生效 level
    out_frames = []
    for frame in data["frames"]:
        by_id = {t["id"]: t for t in frame["tiles"]}
        # 1) 屏幕误差 + 滞回选层; 未加载回退祖先覆盖(level 0)
        levels = {}
        for tid in sorted(by_id):
            t = by_id[tid]
            if not t.get("ready", False):
                levels[tid] = 0
            else:
                levels[tid] = select_level(prev.get(tid), t["error"])
        # 2) 相邻差约束, 然后才生成网格
        levels = relax_levels(by_id, levels)
        pos = {(t["x"], t["y"]): t["id"] for t in by_id.values()}
        out_tiles = []
        for tid in sorted(by_id):
            t = by_id[tid]
            lvl = levels[tid]
            coarse = {side for side, dx, dy in SIDES
                      if pos.get((t["x"] + dx, t["y"] + dy)) is not None
                      and levels[pos[(t["x"] + dx, t["y"] + dy)]] < lvl}
            mesh = build_mesh(t["x"], t["y"], lvl, coarse)
            out_tiles.append({
                "id": tid,
                "level": lvl,
                "bounds": [t["x"], t["y"], t["x"] + 1, t["y"] + 1],
                "vertices": mesh["vertices"],
                "faces": mesh["faces"],
                "edges": mesh["edges"],
                "triangles": len(mesh["faces"]),
                "coverage": "self" if t.get("ready", False) else "ancestor",
                "transition": prev.get(tid) != lvl,
                "seams": sorted(coarse),
            })
        prev = {tid: levels[tid] for tid in levels}
        out_frames.append({"tiles": out_tiles,
                           "triangles": sum(t["triangles"] for t in out_tiles)})
    return {"frames": out_frames}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("scene",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(replay(json.loads(a.scene.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
