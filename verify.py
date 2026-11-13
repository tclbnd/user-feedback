"""Acceptance checks for the progressive mesh codec (see README)."""
import json
import math
import random
import sys
from collections import defaultdict

import progressive as P

FAILURES = []


def check(name, cond, msg=""):
    print("%s %s %s" % ("PASS" if cond else "FAIL", name, msg))
    if not cond:
        FAILURES.append(name)


def canon(mesh):
    return json.dumps(P.canonical_mesh(mesh), sort_keys=True)


def undirected_edges(mesh):
    edges = set()
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            a, b = v[i], v[(i + 1) % 3]
            edges.add((min(a, b), max(a, b)))
    return edges


def components(mesh):
    parent = dict((f["id"], f["id"]) for f in mesh["faces"])

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    ef = defaultdict(list)
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            a, b = v[i], v[(i + 1) % 3]
            ef[(min(a, b), max(a, b))].append(f["id"])
    for e in ef:
        fs = ef[e]
        for g in fs[1:]:
            parent[find(fs[0])] = find(g)
    return len(set(find(f["id"]) for f in mesh["faces"]))


def boundary_loops(mesh):
    ef = defaultdict(int)
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            ef[(v[i], v[(i + 1) % 3])] += 1
    bnd = [e for e in ef if ef[e] == 1 and (e[1], e[0]) not in ef]
    out = defaultdict(list)
    for a, b in bnd:
        out[a].append((a, b))
    loops = []
    used = set()
    for e in bnd:
        if e in used:
            continue
        loop = [e[0]]
        cur = e
        while cur not in used:
            used.add(cur)
            nxt = [x for x in out[cur[1]] if x not in used]
            if not nxt:
                break
            cur = nxt[0]
            loop.append(cur[0])
        loops.append(loop)
    return loops


def orientation_ok(mesh):
    dirc = defaultdict(int)
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            dirc[(v[i], v[(i + 1) % 3])] += 1
    return all(c == 1 for c in dirc.values())


def material_boundaries(mesh):
    ef = defaultdict(list)
    frec = dict((f["id"], f) for f in mesh["faces"])
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            a, b = v[i], v[(i + 1) % 3]
            ef[(min(a, b), max(a, b))].append(f["id"])
    out = set()
    for e, fs in ef.items():
        if len(fs) == 2 and frec[fs[0]]["material"] != frec[fs[1]]["material"]:
            out.add(e)
    return out


def uv_seams(mesh):
    ef = defaultdict(list)
    frec = dict((f["id"], f) for f in mesh["faces"])
    for f in mesh["faces"]:
        v = f["v"]
        for i in range(3):
            a, b = v[i], v[(i + 1) % 3]
            ef[(min(a, b), max(a, b))].append(f["id"])
    out = set()
    for e, fs in ef.items():
        if len(fs) != 2:
            continue
        a, b = e
        for x in (a, b):
            uvs = []
            for fid in fs:
                f = frec[fid]
                uvs.append(tuple(f["uv"][f["v"].index(x)]))
            if uvs[0] != uvs[1]:
                out.add(e)
                break
    return out


def positions(mesh):
    return dict((v["id"], tuple(v["p"])) for v in mesh["vertices"])


def folded_sheet():
    """Sheet folded 60 deg along i=4 (hard edge), 2x2 hole, UV seam on the
    fold, material boundary along j=4."""
    n = 8
    cos60 = 0.5
    sin60 = math.sqrt(3.0) * 0.5
    verts = []
    for j in range(n + 1):
        for i in range(n + 1):
            vid = j * (n + 1) + i
            if i <= 4:
                p = [float(i), float(j), 0.0]
            else:
                dx = i - 4
                p = [4.0 + dx * cos60, float(j), dx * sin60]
            verts.append(dict(id=vid, p=p))
    faces = []
    fid = 0
    hole = set([(1, 1), (1, 2), (2, 1), (2, 2)])
    for j in range(n):
        for i in range(n):
            if (i, j) in hole:
                continue
            a = j * (n + 1) + i
            b = a + 1
            c = a + (n + 1)
            d = c + 1
            off = 0.5 if i >= 4 else 0.0
            for tri in ((a, b, d), (a, d, c)):
                uv = []
                for x in tri:
                    ii = x % (n + 1)
                    jj = x // (n + 1)
                    uv.append([ii / 8.0 + off, jj / 8.0])
                faces.append(dict(id=fid, v=list(tri), uv=uv,
                                  material="lower" if j < 4 else "upper"))
                fid += 1
    return dict(vertices=verts, faces=faces)


def bumpy_sheet():
    n = 6
    verts = []
    for j in range(n + 1):
        for i in range(n + 1):
            z = 0.25 * math.sin(i * 1.7) + 0.25 * math.cos(j * 1.3)
            verts.append(dict(id=j * (n + 1) + i,
                              p=[float(i), float(j), z]))
    faces = []
    fid = 0
    for j in range(n):
        for i in range(n):
            a = j * (n + 1) + i
            b = a + 1
            c = a + (n + 1)
            d = c + 1
            for tri in ((a, b, d), (a, d, c)):
                uv = [[x % (n + 1) / 6.0, x // (n + 1) / 6.0] for x in tri]
                faces.append(dict(id=fid, v=list(tri), uv=uv, material="m"))
                fid += 1
    return dict(vertices=verts, faces=faces)


def quad_mesh():
    verts = [dict(id=0, p=[0.0, 0.0, 0.0]), dict(id=1, p=[1.0, 0.0, 0.0]),
             dict(id=2, p=[1.0, 1.0, 0.0]), dict(id=3, p=[0.0, 1.0, 0.0])]
    faces = [dict(id=0, v=[0, 1, 2], uv=[[0.0, 0.0], [1.0, 0.0], [1.0, 1.0]],
                  material="m"),
             dict(id=1, v=[0, 2, 3], uv=[[0.0, 0.0], [1.0, 1.0], [0.0, 1.0]],
                  material="m")]
    return dict(vertices=verts, faces=faces)


def check_level_common(tag, stream, idx, orig, exp_loops, prot):
    levels = stream["levels"]
    lev = levels[idx]
    m = P.decode_mesh(stream, idx)
    check("%s.level%d.count" % (tag, idx),
          len(m["faces"]) == lev["count"],
          "count=%d decoded=%d" % (lev["count"], len(m["faces"])))
    check("%s.level%d.components" % (tag, idx), components(m) == 1)
    loops = boundary_loops(m)
    check("%s.level%d.boundary_loops" % (tag, idx),
          len(loops) == exp_loops, "loops=%d" % len(loops))
    check("%s.level%d.orientation" % (tag, idx), orientation_ok(m))
    max_error = stream["stats"]["max_error"]
    check("%s.level%d.error_bound" % (tag, idx),
          lev["error"] <= max_error + 1e-12,
          "error=%g max=%g" % (lev["error"], max_error))
    opos = positions(orig)
    mpos = positions(m)
    moved = [x for x in prot if x not in mpos or mpos[x] != opos[x]]
    check("%s.level%d.protected_fixed" % (tag, idx), not moved,
          "moved=%s" % (moved[:5],))
    obnd = set()
    for loop in boundary_loops(orig):
        obnd.update(loop)
    mbnd = set()
    for loop in loops:
        mbnd.update(loop)
    check("%s.level%d.boundary_vertices" % (tag, idx), obnd == mbnd)
    check("%s.level%d.material_seam" % (tag, idx),
          material_boundaries(orig) <= material_boundaries(m))
    check("%s.level%d.uv_seam" % (tag, idx),
          uv_seams(orig) <= uv_seams(m))
    return m


def test_stream(tag, data, orig, exp_loops, min_levels=3):
    stream = P.encode(data)
    levels = stream["levels"]
    check("%s.levels>=3" % tag, len(levels) >= min_levels,
          "levels=%d" % len(levels))
    check("%s.diffs" % tag, len(stream["diffs"]) >= len(levels),
          "diffs=%d" % len(stream["diffs"]))
    st0 = P.State(P.canonical_mesh(orig))
    prot = P.protected_vertices(st0, float(data.get("hard_edge_angle", 45.0)))
    for i in range(len(levels)):
        check_level_common(tag, stream, i, orig, exp_loops, prot)
    last = P.decode_mesh(stream, len(levels) - 1)
    check("%s.final_exact" % tag, canon(last) == canon(orig))
    # adjacent-level diffs reproduce the next level
    for k in range(len(levels) - 1):
        d = stream["diffs"][k + 1]
        ok = (d["begin"] == levels[k]["split"]
              and d["end"] == levels[k + 1]["split"])
        st = P.State(P.decode_mesh(stream, k))
        for s in stream["splits"][d["begin"]:d["end"]]:
            P.apply_split(st, s)
        ok = ok and canon(P.mesh_out(st)) == canon(P.decode_mesh(stream, k + 1))
        check("%s.diff%d" % (tag, k), ok)
    # deterministic under input permutation
    s0 = json.dumps(stream, sort_keys=True)
    for seed in range(5):
        d2 = json.loads(json.dumps(data))
        random.Random(seed).shuffle(d2["mesh"]["vertices"])
        random.Random(seed + 100).shuffle(d2["mesh"]["faces"])
        s1 = json.dumps(P.encode(d2), sort_keys=True)
        check("%s.determinism.%d" % (tag, seed), s0 == s1)
    # 30 seeded forward/backward jumps vs independent level loads
    rng = random.Random(20240614)
    player = P.Player(stream)
    cur = 0
    ok = True
    for it in range(30):
        r = rng.random()
        if r < 0.35:
            cur = (cur + 1) % len(levels)
        elif r < 0.7:
            cur = (cur - 1) % len(levels)
        else:
            cur = rng.randrange(len(levels))
        got = player.goto(cur)
        want = P.decode_mesh(stream, cur)
        if canon(got) != canon(want):
            ok = False
            print("jump mismatch at iter %d level %d" % (it, cur))
            break
    check("%s.jumps30" % tag, ok)
    return stream


def main():
    # 1) holed thin sheet with duplicated-attribute (material/UV) seam
    data = json.load(open("demo.json"))
    orig = P.canonical_mesh(data["mesh"])
    print("== demo: holed sheet, material/UV seam ==")
    test_stream("demo", data, orig, exp_loops=2)

    # 2) folded holed sheet: hard edge + UV seam + material boundary
    print("== folded sheet: hard edge, hole, seams ==")
    fmesh = folded_sheet()
    fdata = dict(mesh=fmesh, targets=[80, 100, 120], max_error=0.5)
    test_stream("folded", fdata, P.canonical_mesh(fmesh), exp_loops=2)

    # 3) stop reason: error bound prevents further simplification
    # (hard-edge detection disabled so only the error bound constrains)
    print("== stop reason: max_error ==")
    bmesh = bumpy_sheet()
    bdata = dict(mesh=bmesh, targets=[40], max_error=1e-9,
                 hard_edge_angle=180.0)
    sb = P.encode(bdata)
    check("bumpy.stop_max_error",
          sb["stats"]["stop"].startswith("max_error"), sb["stats"]["stop"])
    check("bumpy.unreachable", sb["stats"]["unreachable"] == [40])
    check("bumpy.final_exact",
          canon(P.decode_mesh(sb, -1)) == canon(bmesh))

    # 4) stop reason: no legal collapse (all vertices protected)
    print("== stop reason: no legal collapse ==")
    qdata = dict(mesh=quad_mesh(), targets=[0], max_error=10.0)
    sq = P.encode(qdata)
    check("quad.stop_no_legal",
          sq["stats"]["stop"].startswith("no_legal_collapse"),
          sq["stats"]["stop"])
    check("quad.final_exact",
          canon(P.decode_mesh(sq, -1)) == canon(quad_mesh()))

    # 5) unreachable target on demo: simplify until topology blocks
    print("== unreachable target: report and keep boundary loops ==")
    d3 = dict(mesh=data["mesh"], targets=[2], max_error=data["max_error"])
    s3 = P.encode(d3)
    check("demo2.stop_no_legal",
          s3["stats"]["stop"].startswith("no_legal_collapse"),
          s3["stats"]["stop"])
    check("demo2.unreachable", s3["stats"]["unreachable"] == [2])
    base = P.decode_mesh(s3, 0)
    check("demo2.base_components", components(base) == 1)
    check("demo2.base_loops", len(boundary_loops(base)) == 2)
    check("demo2.base_orientation", orientation_ok(base))
    check("demo2.final_exact", canon(P.decode_mesh(s3, -1)) == canon(orig))

    print()
    if FAILURES:
        print("FAILED: %d checks" % len(FAILURES))
        for f in FAILURES:
            print(" -", f)
        sys.exit(1)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    main()
