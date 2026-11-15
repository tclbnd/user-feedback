import hashlib
import json
import math
from pathlib import Path
import sys

# 相对容差：随场景坐标尺度缩放，保证闭合触碰不被浮点误差误判为分离
EPS_REL = 1e-10

_UNIT_AXES = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _is_zero(v):
    return v[0] == 0.0 and v[1] == 0.0 and v[2] == 0.0


def prepare_triangle(tri):
    """预计算三角面的 SAT 轴及各轴上的顶点投影区间。

    轴集合与具体候选立方体无关，每张面只需计算一次：
    3 条立方体法线 + 1 条三角面法线 + 9 条（棱 x 棱）叉积轴。
    """
    a, b, c = tri
    e1 = _sub(b, a)
    e2 = _sub(c, a)
    e3 = _sub(c, b)

    axes = list(_UNIT_AXES)
    normal = _cross(e1, e2)
    if not _is_zero(normal):
        axes.append(normal)
    for edge in (e1, e2, e3):
        for unit in _UNIT_AXES:
            candidate = _cross(unit, edge)
            if not _is_zero(candidate):
                axes.append(candidate)

    prepared = []
    for axis in axes:
        p0 = _dot(a, axis)
        p1 = _dot(b, axis)
        p2 = _dot(c, axis)
        prepared.append((axis, min(p0, p1, p2), max(p0, p1, p2)))
    return prepared


def cube_touch(prepared, i, j, k, tol):
    """三角面与单位闭立方体 [i,i+1]x... 是否触碰（SAT，含边界）。

    任意候选轴上投影区间严格分离才判不相交，因此只碰到顶点、边、面
    的边角情形也返回 True。
    """
    cx, cy, cz = i + 0.5, j + 0.5, k + 0.5
    for axis, t_min, t_max in prepared:
        ax, ay, az = axis
        center_proj = ax * cx + ay * cy + az * cz
        # 单位立方体半边长为 0.5，投影半径为 0.5 * L1(axis)
        radius = 0.5 * (abs(ax) + abs(ay) + abs(az))
        lo = t_min - center_proj
        hi = t_max - center_proj
        slack = tol * (abs(ax) + abs(ay) + abs(az))
        if lo > radius + slack or hi < -radius - slack:
            return False
    return True


def candidate_bounds(tri, resolution):
    """返回与三角面轴对齐包围盒闭区间相交的体素索引范围（外扩一层）。"""
    mins = [min(v[d] for v in tri) for d in range(3)]
    maxs = [max(v[d] for v in tri) for d in range(3)]
    bounds = []
    for lo, hi in zip(mins, maxs):
        i_lo = max(0, math.floor(lo) - 1)
        i_hi = min(resolution - 1, math.ceil(hi))
        bounds.append((i_lo, i_hi))
    return bounds


def collect_leaves(data):
    """SAT 扫描全部三角面，返回 (排序后的占用叶集合, 每面扫描统计)。"""
    resolution = data["resolution"]
    triangles = [tuple(tuple(float(v) for v in vert) for vert in tri)
                 for tri in data["triangles"]]

    scale = 1.0
    for tri in triangles:
        for vert in tri:
            for value in vert:
                scale = max(scale, abs(value))
    tol = EPS_REL * scale

    occupied = set()
    scan_stats = []
    for tri in triangles:
        prepared = prepare_triangle(tri)
        (x0, x1), (y0, y1), (z0, z1) = candidate_bounds(tri, resolution)
        tested = 0
        before = len(occupied)
        for i in range(x0, x1 + 1):
            for j in range(y0, y1 + 1):
                for k in range(z0, z1 + 1):
                    tested += 1
                    if cube_touch(prepared, i, j, k, tol):
                        occupied.add((i, j, k))
        scan_stats.append({
            "candidates_tested": tested,
            "voxels_added": len(occupied) - before,
        })
    return sorted(occupied), scan_stats


def _sorted_nodes(node_map):
    return [node_map[key] for key in sorted(node_map)]


def build_octree(leaves, depth):
    """自底向上归并非空叶子，构建稀疏八叉树。

    levels[depth] 为叶层，levels[0] 为根层。内部节点 child_mask 为
    子节点位掩码之并，occupied_leaves 为子节点占用叶数之和；全程
    使用稀疏字典，不分配完整网格。
    """
    levels = [None] * (depth + 1)
    merge_stats = []

    current = {}
    for x, y, z in leaves:
        current[(x, y, z)] = {
            "x": x, "y": y, "z": z,
            "child_mask": 0,
            "occupied_leaves": 1,
        }
    levels[depth] = _sorted_nodes(current)

    for d in range(depth, 0, -1):
        parents = {}
        max_children = 0
        full_parents = 0
        for (x, y, z), node in current.items():
            key = (x >> 1, y >> 1, z >> 1)
            bit = (x & 1) + 2 * (y & 1) + 4 * (z & 1)
            parent = parents.get(key)
            if parent is None:
                parent = {
                    "x": key[0], "y": key[1], "z": key[2],
                    "child_mask": 0,
                    "occupied_leaves": 0,
                    "_children": 0,
                }
                parents[key] = parent
            parent["child_mask"] |= 1 << bit
            parent["occupied_leaves"] += node["occupied_leaves"]
            parent["_children"] += 1

        child_node_count = len(current)
        for parent in parents.values():
            if parent["_children"] > max_children:
                max_children = parent["_children"]
            if parent["child_mask"] == 0xFF:
                full_parents += 1
            del parent["_children"]

        merge_stats.append({
            "parent_depth": d - 1,
            "child_depth": d,
            "child_nodes": child_node_count,
            "parent_nodes": len(parents),
            "children_merged": child_node_count - len(parents),
            "max_children_per_parent": max_children,
            "full_parents": full_parents,
        })
        current = parents
        levels[d - 1] = _sorted_nodes(current)

    return levels, merge_stats


def voxelize(data):
    resolution = data["resolution"]
    depth = resolution.bit_length() - 1
    if resolution != 1 << depth:
        raise ValueError("resolution 必须是 2 的幂")

    leaves, scan_stats = collect_leaves(data)
    levels, merge_stats = build_octree(leaves, depth)

    result_leaves = [list(cell) for cell in leaves]
    result_levels = [
        {"depth": d, "count": len(levels[d]), "nodes": levels[d]}
        for d in range(depth + 1)
    ]

    structure = {"leaves": result_leaves, "levels": result_levels}
    canonical = json.dumps(structure, sort_keys=True).encode()
    leaf_hash = hashlib.sha256(
        json.dumps(result_leaves, sort_keys=True).encode()
    ).hexdigest()
    digest = hashlib.sha256(canonical).hexdigest()

    return {
        "resolution": resolution,
        "leaves": result_leaves,
        "levels": result_levels,
        "merge_stats": merge_stats,
        "scan_stats": scan_stats,
        "leaf_hash": leaf_hash,
        "digest": digest,
    }


def print_summary(result, stream=sys.stderr):
    header = "resolution={} leaves={} leaf_hash={}".format(
        result["resolution"], len(result["leaves"]),
        result["leaf_hash"][:16])
    print(header, file=stream)
    for level in result["levels"]:
        print("  level depth={} nodes={}".format(
            level["depth"], level["count"]), file=stream)
    for stat in result["merge_stats"]:
        print(
            "  merge depth {child_depth}->{parent_depth}: "
            "child_nodes={child_nodes} parent_nodes={parent_nodes} "
            "merged={children_merged} max_children={max_children_per_parent} "
            "full_parents={full_parents}".format(**stat),
            file=stream)


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    output = voxelize(data)
    Path(sys.argv[2]).write_text(json.dumps(output, sort_keys=True))
    print_summary(output)
