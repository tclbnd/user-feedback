"""voxelize.py 的验证套件。

参考实现是一套独立写法的通用凸多面体 SAT（直接枚举三角面与立方体
的所有棱并两两叉积），在完整 R^3 网格上暴力枚举，作为“精确参考”；
被测实现只扫描包围盒候选体素，二者体素集合必须逐元素相同。
"""

import hashlib
import json
import random

import voxelize as vox


# ---------- 独立参考 SAT（与 voxelize.py 不同的写法） ----------

def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _unique(axes):
    out = []
    for axis in axes:
        norm = sum(v * v for v in axis) ** 0.5
        if norm == 0.0:
            continue
        unit = tuple(v / norm for v in axis)
        if not any(abs(_dot(unit, q)) > 0.9999999 for q in out):
            out.append(unit)
    return out


def reference_touch(tri, i, j, k):
    """全轴 SAT：三角面与闭立方体是否相交/触碰。"""
    cube = [
        (i + dx, j + dy, k + dz)
        for dx in (0, 1) for dy in (0, 1) for dz in (0, 1)
    ]
    axes = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    tri_edges = [_sub(tri[a], tri[b])
                 for a, b in ((0, 1), (1, 2), (2, 0))]
    # 立方体 12 条棱只有 3 个方向，叉积天然含重复，交给 _unique 去重
    cube_dirs = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
    for te in tri_edges:
        for ce in cube_dirs:
            axes.append(_cross(te, ce))
    axes = _unique(axes)

    eps = 1e-9
    for axis in axes:
        tp = [_dot(p, axis) for p in tri]
        cp = [_dot(p, axis) for p in cube]
        if min(tp) > max(cp) + eps or min(cp) > max(tp) + eps:
            return False
    return True


def reference_voxel_set(data):
    r = data["resolution"]
    result = set()
    for tri in data["triangles"]:
        tri = [tuple(map(float, v)) for v in tri]
        for i in range(r):
            for j in range(r):
                for k in range(r):
                    if reference_touch(tri, i, j, k):
                        result.add((i, j, k))
    return result


# ---------- 测试网格：薄片、细杆、边角触碰 ----------

def thin_sheet(r):
    # z=0 平面上的大薄片，跨越多个体素但没有厚度
    return [[[0.5, 0.5, 0.5], [r - 0.5, 0.5, 0.5], [r - 0.5, r - 0.5, 0.5]]]


def diagonal_rod(r):
    # 沿体对角线方向的细杆（极窄三角形），穿过一连串体素内部
    return [[[0.5, 0.5, 0.5], [r - 0.5, r - 0.5, r - 0.5],
             [0.52, 0.5, 0.5]]]


def corner_triangle(r):
    # 只在格点 (2,2,2) 处触碰体素 (1,1,1) 的小三角形，整体位于其外
    return [[[2.0, 2.0, 2.0], [3.5, 2.0, 2.0], [2.0, 3.5, 2.0]]]


def edge_grazer(r):
    # 沿体素 (3,3,3) 的一条棱滑行的退化（共线）三角形
    return [[[2.5, 3.0, 3.0], [4.5, 3.0, 3.0], [4.0, 3.0, 3.0]]]


def face_grazer(r):
    # 贴在平面 x=4 上的薄片，同时触碰两侧体素
    return [[[4.0, 1.5, 1.5], [4.0, 5.5, 1.5], [4.0, 1.5, 5.5]]]


def outside_triangle(r):
    # 完全在工作域外，必须贡献零体素
    return [[[r + 1, r + 1, r + 1], [r + 2, r + 1, r + 1],
             [r + 1, r + 2, r + 1]]]


def complex_mesh(r):
    return (thin_sheet(r) + diagonal_rod(r) + corner_triangle(r)
            + edge_grazer(r) + face_grazer(r) + outside_triangle(r))


MESHS = {
    "thin_sheet": thin_sheet,
    "diagonal_rod": diagonal_rod,
    "corner_touch": corner_triangle,
    "edge_grazer": edge_grazer,
    "face_grazer": face_grazer,
    "outside": outside_triangle,
    "complex": complex_mesh,
}


# ---------- 结构校验 ----------

def check_structure(result, leaves, r):
    depth = r.bit_length() - 1
    assert result["resolution"] == r
    assert result["leaves"] == [list(c) for c in leaves]
    assert len(result["levels"]) == depth + 1

    # 叶层与 leaves 一致
    leaf_level = result["levels"][depth]
    assert leaf_level["count"] == len(leaves)
    assert [(n["x"], n["y"], n["z"]) for n in leaf_level["nodes"]] == leaves
    for node in leaf_level["nodes"]:
        assert node["child_mask"] == 0
        assert node["occupied_leaves"] == 1

    # 各层排序、父子关系与自底向上归并规则
    for d in range(depth + 1):
        level = result["levels"][d]
        coords = [(n["x"], n["y"], n["z"]) for n in level["nodes"]]
        assert coords == sorted(coords)
        assert level["count"] == len(coords)

    # 独立重算每一层，与输出节点逐一比对
    current = {cell: 1 for cell in leaves}  # 坐标 -> 子树占用叶数
    for d in range(depth, 0, -1):
        expected = {}
        for (x, y, z), subtree_leaves in current.items():
            key = (x >> 1, y >> 1, z >> 1)
            bit = (x & 1) + 2 * (y & 1) + 4 * (z & 1)
            ent = expected.setdefault(key, [0, 0])
            ent[0] |= 1 << bit           # child_mask：直接子节点位并
            ent[1] += subtree_leaves     # occupied_leaves：叶数求和
        actual = {(n["x"], n["y"], n["z"]): n
                  for n in result["levels"][d - 1]["nodes"]}
        assert set(actual) == set(expected)
        for key, (mask, leaf_count) in expected.items():
            node = actual[key]
            assert node["child_mask"] == mask
            assert node["occupied_leaves"] == leaf_count
        current = {key: leaf_count for key, (_, leaf_count) in
                   expected.items()}

    # 根层唯一且覆盖全部叶
    if leaves:
        assert result["levels"][0]["count"] == 1
        root = result["levels"][0]["nodes"][0]
        assert (root["x"], root["y"], root["z"]) == (0, 0, 0)
        assert root["occupied_leaves"] == len(leaves)

    # 合并统计与层级计数自洽
    child_counts = [len(current_level["nodes"]) for current_level in
                    result["levels"]]
    for stat in result["merge_stats"]:
        pd, cd = stat["parent_depth"], stat["child_depth"]
        assert stat["parent_nodes"] == child_counts[pd]
        assert stat["child_nodes"] == child_counts[cd]
        assert (stat["children_merged"]
                == child_counts[cd] - child_counts[pd])
        if child_counts[pd] > 0:
            assert 1 <= stat["max_children_per_parent"] <= 8
        else:
            assert stat["max_children_per_parent"] == 0


def run_case(name, r, seed):
    data = {"resolution": r, "triangles": MESHS[name](r)}
    result = vox.voxelize(data)
    leaves = [tuple(c) for c in result["leaves"]]
    reference = reference_voxel_set(data)

    assert set(leaves) == reference, (
        name, r, "mismatch",
        "missing:", sorted(reference - set(leaves))[:5],
        "extra:", sorted(set(leaves) - reference)[:5])

    check_structure(result, sorted(reference), r)

    # 随机重排面表（含重复面），叶哈希与整体摘要必须不变
    rng = random.Random(seed)
    shuffled = list(data["triangles"])
    rng.shuffle(shuffled)
    shuffled += shuffled[:2]
    rng.shuffle(shuffled)
    result2 = vox.voxelize({"resolution": r, "triangles": shuffled})
    assert result2["leaf_hash"] == result["leaf_hash"]
    assert result2["digest"] == result["digest"]
    assert result2["leaves"] == result["leaves"]
    assert result2["levels"] == result["levels"]

    # 同输入复跑，层级摘要一致
    result3 = vox.voxelize(json.loads(json.dumps(data)))
    assert result3["digest"] == result["digest"]
    return result


def main():
    resolutions = (8, 16)
    summary = {}
    for r in resolutions:
        for name in sorted(MESHS):
            result = run_case(name, r, seed=1000 + r)
            summary[(name, r)] = (
                len(result["leaves"]),
                [level["count"] for level in result["levels"]],
                result["leaf_hash"][:12],
            )
            print("ok r={:2d} {:12s} leaves={:4d} levels={}".format(
                r, name, len(result["leaves"]),
                [level["count"] for level in result["levels"]]))

    # 两组分辨率复跑：同输入重复执行，摘要稳定
    for name in sorted(MESHS):
        for r in resolutions:
            data = {"resolution": r, "triangles": MESHS[name](r)}
            a = vox.voxelize(data)
            b = vox.voxelize(data)
            assert a["digest"] == b["digest"]

    # 边界情形：空面表与 resolution=1
    empty = vox.voxelize({"resolution": 8, "triangles": []})
    assert empty["leaves"] == []
    assert all(level["count"] == 0 for level in empty["levels"])
    assert empty["merge_stats"]
    one = vox.voxelize({"resolution": 1,
                        "triangles": [[[0.0, 0.0, 0.0],
                                       [1.0, 0.0, 0.0],
                                       [0.0, 1.0, 0.0]]]})
    assert one["leaves"] == [[0, 0, 0]]
    assert len(one["levels"]) == 1

    # 压力：更大分辨率下的细杆仍与参考一致且能快速完成
    data = {"resolution": 32, "triangles": complex_mesh(32)}
    big = vox.voxelize(data)
    assert set(map(tuple, big["leaves"])) == reference_voxel_set(data)
    print("ok r=32 complex leaves={}".format(len(big["leaves"])))

    print("\nALL TESTS PASSED ({} mesh-resolution cases)".format(
        len(summary) + 1))


if __name__ == "__main__":
    main()