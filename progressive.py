"""Progressive triangle mesh codec.

encode: QEM edge collapses constrained so that material boundaries, UV
island seams, hard edges and open boundary loops are preserved exactly;
candidates that flip a face or break the link condition / create duplicate
faces are rejected.  Candidate order is fully deterministic and does not
depend on the input ordering of vertex/face lists.

decode: replay vertex-split records (coarse -> fine).  Vertex and face ids
and per-corner attributes are stable; the finest level reproduces the input
mesh exactly.  A Player can jump to a level and keep reading increments.

Stream JSON (meshes: dict(vertices=[dict(id,p)], faces=[dict(id,v,uv,material)])):
  format "pmesh1"
  base   coarsest mesh
  splits coarse->fine vertex split records; level mesh = base + splits[:split]
  levels coarse->fine list of dict(count, split, error); count == decoded faces
  diffs  adjacent-level split ranges dict(src, dst, begin, end, num)
  stats  dict(stop, collapses, unreachable, ...)
"""
import argparse
import heapq
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

EPS_AREA = 1e-12
COS_FLIP = 1e-6
UV_EPS = 1e-9


def vsub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def vcross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vlen(a):
    return math.sqrt(vdot(a, a))


# quadric = (axx, axy, axz, ayy, ayz, azz, bx, by, bz, c)
ZERO_Q = (0.0,) * 10


def qadd(q1, q2):
    return tuple(a + b for a, b in zip(q1, q2))


def qeval(q, p):
    axx, axy, axz, ayy, ayz, azz, bx, by, bz, c = q
    x, y, z = p
    return (axx * x * x + 2.0 * axy * x * y + 2.0 * axz * x * z
            + ayy * y * y + 2.0 * ayz * y * z + azz * z * z
            + 2.0 * (bx * x + by * y + bz * z) + c)


def face_quadric(p0, p1, p2):
    n = vcross(vsub(p1, p0), vsub(p2, p0))
    l = vlen(n)
    if l <= EPS_AREA:
        return None
    w = 0.5 * l
    nx, ny, nz = n[0] / l, n[1] / l, n[2] / l
    d = -(nx * p0[0] + ny * p0[1] + nz * p0[2])
    return (w * nx * nx, w * nx * ny, w * nx * nz,
            w * ny * ny, w * ny * nz, w * nz * nz,
            w * d * nx, w * d * ny, w * d * nz, w * d * d)


def canonical_mesh(mesh):
    """Sort vertices/faces by id and validate; input-order independent."""
    verts = sorted(mesh["vertices"], key=lambda r: r["id"])
    faces = sorted(mesh["faces"], key=lambda r: r["id"])
    vids = [v["id"] for v in verts]
    if len(set(vids)) != len(vids):
        raise ValueError("duplicate vertex id")
    fids = [f["id"] for f in faces]
    if len(set(fids)) != len(fids):
        raise ValueError("duplicate face id")
    vset = set(vids)
    for f in faces:
        if len(f["v"]) != 3 or len(set(f["v"])) != 3:
            raise ValueError("face %r is not a proper triangle" % (f["id"],))
        for x in f["v"]:
            if x not in vset:
                raise ValueError("face %r misses vertex %r" % (f["id"], x))
        if len(f["uv"]) != 3:
            raise ValueError("face %r needs 3 corner uvs" % (f["id"],))
    return dict(
        vertices=[dict(id=v["id"], p=[float(c) for c in v["p"]])
                  for v in verts],
        faces=[dict(id=f["id"], v=[int(x) for x in f["v"]],
                    uv=[[float(c) for c in t] for t in f["uv"]],
                    material=f["material"]) for f in faces])


class State(object):
    def __init__(self, mesh):
        self.pos = dict((v["id"], tuple(v["p"])) for v in mesh["vertices"])
        self.frec = dict((f["id"], dict(v=list(f["v"]),
                                        uv=[list(t) for t in f["uv"]],
                                        material=f["material"]))
                         for f in mesh["faces"])
        self.v2f = defaultdict(set)
        for fid in sorted(self.frec):
            for x in self.frec[fid]["v"]:
                self.v2f[x].add(fid)
        self.ver = defaultdict(int)

    def neighbors(self, x):
        out = set()
        for f in self.v2f[x]:
            out.update(self.frec[f]["v"])
        out.discard(x)
        return out


def mesh_out(st):
    return dict(
        vertices=[dict(id=i, p=list(st.pos[i])) for i in sorted(st.pos)],
        faces=[dict(id=f, v=list(r["v"]), uv=[list(t) for t in r["uv"]],
                    material=r["material"])
               for f, r in sorted(st.frec.items())])


def edge_map(st):
    ef = defaultdict(list)
    for fid in sorted(st.frec):
        v = st.frec[fid]["v"]
        for i in range(3):
            a, b = v[i], v[(i + 1) % 3]
            ef[(min(a, b), max(a, b))].append(fid)
    return ef


def face_normal(pos, tri):
    return vcross(vsub(pos[tri[1]], pos[tri[0]]),
                  vsub(pos[tri[2]], pos[tri[0]]))


def protected_vertices(st, hard_angle_deg):
    """Vertices on open-boundary, material-boundary, UV-seam or hard
    (sharp dihedral) edges.  Never removed nor moved by a collapse."""
    ef = edge_map(st)
    cos_t = math.cos(math.radians(hard_angle_deg))
    prot = set()
    for e in sorted(ef):
        fs = ef[e]
        a, b = e
        hard = False
        if len(fs) != 2:
            hard = True  # open boundary or non-manifold edge
        else:
            r0, r1 = st.frec[fs[0]], st.frec[fs[1]]
            if r0["material"] != r1["material"]:
                hard = True
            else:
                for x in (a, b):
                    u0 = r0["uv"][r0["v"].index(x)]
                    u1 = r1["uv"][r1["v"].index(x)]
                    if (abs(u0[0] - u1[0]) > UV_EPS
                            or abs(u0[1] - u1[1]) > UV_EPS):
                        hard = True
                        break
                if not hard:
                    n0 = face_normal(st.pos, r0["v"])
                    n1 = face_normal(st.pos, r1["v"])
                    l0, l1 = vlen(n0), vlen(n1)
                    if l0 <= EPS_AREA or l1 <= EPS_AREA:
                        hard = True
                    elif vdot(n0, n1) < cos_t * l0 * l1:
                        hard = True
        if hard:
            prot.add(a)
            prot.add(b)
    return prot


def best_position(q, spots):
    best = None
    for p in spots:
        c = qeval(q, p)
        if best is None or c < best[0]:
            best = (c, p)
    return best


def push(heap, st, quad, prot, u, v):
    """Candidate: directed collapse, survivor u, removed v."""
    if v in prot or u not in st.pos or v not in st.pos:
        return
    q = qadd(quad[u], quad[v])
    if u in prot:
        spots = [st.pos[u]]  # protected survivor must not move
    else:
        pu, pv = st.pos[u], st.pos[v]
        spots = [pu, pv, ((pu[0] + pv[0]) * 0.5,
                          (pu[1] + pv[1]) * 0.5,
                          (pu[2] + pv[2]) * 0.5)]
    cost, p = best_position(q, spots)
    heapq.heappush(heap, (cost, u, v, p, st.ver[u], st.ver[v]))


def legal(st, u, v, newpos):
    """Link condition, no flips, no degenerate or duplicate faces."""
    if u not in st.pos or v not in st.pos:
        return False
    fu, fv = st.v2f[u], st.v2f[v]
    both = fu & fv
    if len(both) != 2:
        return False  # not an interior edge
    opp = set()
    for f in both:
        for x in st.frec[f]["v"]:
            if x != u and x != v:
                opp.add(x)
    if len(opp) != 2:
        return False
    shared = (st.neighbors(u) & st.neighbors(v)) - set((u, v))
    if shared != opp:
        return False  # link condition: non-manifold/duplicate would result
    surviving = (fu | fv) - both
    seen = set()
    for f in sorted(surviving):
        rec = st.frec[f]
        old = [st.pos[x] for x in rec["v"]]
        new = [newpos if x == u or x == v else st.pos[x] for x in rec["v"]]
        n0 = vcross(vsub(old[1], old[0]), vsub(old[2], old[0]))
        n1 = vcross(vsub(new[1], new[0]), vsub(new[2], new[0]))
        l0, l1 = vlen(n0), vlen(n1)
        if l1 <= EPS_AREA:
            return False  # degenerate
        if l0 > EPS_AREA and vdot(n0, n1) <= COS_FLIP * l0 * l1:
            return False  # flipped normal
        t = tuple(sorted(u if x == v else x for x in rec["v"]))
        if t in seen:
            return False  # duplicate face
        seen.add(t)
    # Any face duplicating a new triple must contain u, hence is in
    # surviving or both and was already compared above.
    return True


def copy_rec(fid, r):
    return dict(id=fid, v=list(r["v"]), uv=[list(t) for t in r["uv"]],
                material=r["material"])


def apply_collapse(st, quad, u, v, newpos):
    fu = set(st.v2f[u])
    fv = set(st.v2f[v])
    both = fu & fv
    mod = sorted(fv - both)
    both = sorted(both)
    rec = dict(u=u, v=v, old_u=st.pos[u], pos_v=st.pos[v],
               removed=[copy_rec(f, st.frec[f]) for f in both],
               modified=[copy_rec(f, st.frec[f]) for f in mod])
    old_nv = st.neighbors(v)
    for f in both:
        for x in st.frec[f]["v"]:
            st.v2f[x].discard(f)
        del st.frec[f]
    for f in mod:
        r = st.frec[f]
        r["v"] = [u if x == v else x for x in r["v"]]
        st.v2f[v].discard(f)
        st.v2f[u].add(f)
    del st.pos[v]
    st.pos[u] = newpos
    quad[u] = qadd(quad[u], quad[v])
    del quad[v]
    st.ver[u] += 1
    return rec, old_nv


def encode(data):
    mesh = canonical_mesh(data["mesh"])
    targets = sorted(set(int(t) for t in data.get("targets", [])))
    max_error = float(data.get("max_error", float("inf")))
    hard_angle = float(data.get("hard_edge_angle", 45.0))

    st = State(mesh)
    prot = protected_vertices(st, hard_angle)
    quad = dict((x, ZERO_Q) for x in st.pos)
    for fid in sorted(st.frec):
        tri = st.frec[fid]["v"]
        q = face_quadric(st.pos[tri[0]], st.pos[tri[1]], st.pos[tri[2]])
        if q is None:
            continue
        for x in tri:
            quad[x] = qadd(quad[x], q)

    heap = []
    ef = edge_map(st)
    for e in sorted(ef):
        if len(ef[e]) != 2:
            continue
        a, b = e
        push(heap, st, quad, prot, a, b)
        push(heap, st, quad, prot, b, a)

    orig_count = len(st.frec)
    min_target = min(targets) if targets else 0
    collapses = []
    stop = None
    while len(st.frec) > min_target:
        item = None
        while heap:
            cand = heapq.heappop(heap)
            cost, u, v, p, vu, vv = cand
            if u not in st.pos or v not in st.pos:
                continue
            if st.ver[u] != vu or st.ver[v] != vv:
                continue
            item = cand
            break
        if item is None:
            stop = ("no_legal_collapse: no constraint-satisfying edge "
                    "collapse remains (%d faces left)" % len(st.frec))
            break
        cost, u, v, p, vu, vv = item
        err = math.sqrt(max(cost, 0.0))
        if err > max_error:
            stop = ("max_error: cheapest legal collapse has error %.6g "
                    "above max_error %.6g (%d faces left)"
                    % (err, max_error, len(st.frec)))
            break
        if not legal(st, u, v, p):
            continue
        rec, old_nv = apply_collapse(st, quad, u, v, p)
        rec["err"] = err
        collapses.append(rec)
        affected = st.neighbors(u) | set(old_nv) | set((u,))
        for y in sorted(affected):
            if y not in st.pos:
                continue
            for z in sorted(st.neighbors(y)):
                push(heap, st, quad, prot, y, z)
                push(heap, st, quad, prot, z, y)
    if stop is None:
        stop = "targets_reached: face count %d within target %d" % (
            len(st.frec), min_target)
    return build_stream(st, mesh, collapses, targets, max_error,
                        len(prot), stop, orig_count)


def build_stream(st, mesh, collapses, targets, max_error, nprot, stop,
                 orig_count):
    n = len(collapses)
    counts = [orig_count]
    errs = [0.0]
    for c in collapses:
        counts.append(counts[-1] - len(c["removed"]))
        errs.append(max(errs[-1], c["err"]))

    levels = []
    unreachable = []
    for t in targets:
        idx = None
        for i in range(len(counts)):
            if counts[i] <= t:
                idx = i
                break
        if idx is None:
            unreachable.append(t)
            continue
        lev = dict(count=counts[idx], split=n - idx, error=errs[idx])
        if not levels or levels[-1]["split"] != lev["split"]:
            levels.append(lev)
    if not levels or levels[-1]["count"] != orig_count:
        levels.append(dict(count=orig_count, split=n, error=0.0))

    splits = []
    for c in reversed(collapses):
        add = sorted(c["removed"] + c["modified"], key=lambda r: r["id"])
        splits.append(dict(u=c["u"], v=c["v"],
                           pu=list(c["old_u"]), pv=list(c["pos_v"]),
                           rm=[r["id"] for r in c["modified"]],
                           add=add))

    diffs = []
    prev = 0
    src = "base"
    for i, lev in enumerate(levels):
        diffs.append(dict(src=src, dst=i, begin=prev, end=lev["split"],
                          num=lev["split"] - prev))
        prev = lev["split"]
        src = i

    base = mesh_out(st)
    stats = dict(stop=stop, collapses=n, max_error=max_error,
                 targets=targets, unreachable=unreachable,
                 protected=nprot, faces=orig_count,
                 base_faces=len(base["faces"]))
    return dict(format="pmesh1", base=base, splits=splits, levels=levels,
                diffs=diffs, stats=stats)


def apply_split(st, s):
    u, v = s["u"], s["v"]
    for f in s["rm"]:
        rec = st.frec.pop(f)
        for x in rec["v"]:
            st.v2f[x].discard(f)
    st.pos[v] = tuple(s["pv"])
    st.pos[u] = tuple(s["pu"])
    st.v2f[v] = set()
    for rec in s["add"]:
        r = dict(v=list(rec["v"]), uv=[list(t) for t in rec["uv"]],
                 material=rec["material"])
        st.frec[rec["id"]] = r
        for x in r["v"]:
            st.v2f[x].add(rec["id"])


def level_index(stream, level):
    levels = stream["levels"]
    if not levels:
        raise ValueError("stream has no levels")
    idx = level if level >= 0 else len(levels) + level
    if idx < 0 or idx >= len(levels):
        raise ValueError("level %r out of range [0, %d)"
                         % (level, len(levels)))
    return idx


def decode_mesh(stream, level):
    idx = level_index(stream, level)
    k = stream["levels"][idx]["split"]
    st = State(stream["base"])
    for s in stream["splits"][:k]:
        apply_split(st, s)
    return mesh_out(st)


class Player(object):
    """Incremental decoder: jump to any level, then keep reading increments."""

    def __init__(self, stream):
        self.stream = stream
        self.st = State(stream["base"])
        self.k = 0

    def goto(self, level):
        idx = level_index(self.stream, level)
        k = self.stream["levels"][idx]["split"]
        if k < self.k:
            self.st = State(self.stream["base"])
            self.k = 0
        while self.k < k:
            apply_split(self.st, self.stream["splits"][self.k])
            self.k += 1
        return mesh_out(self.st)


def main():
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="op", required=True)
    e = sub.add_parser("encode")
    e.add_argument("mesh", type=Path)
    e.add_argument("stream", type=Path)
    d = sub.add_parser("decode")
    d.add_argument("stream", type=Path)
    d.add_argument("output", type=Path)
    d.add_argument("--level", type=int, default=-1)
    a = p.parse_args()
    if a.op == "encode":
        data = json.loads(a.mesh.read_text())
        stream = encode(data)
        a.stream.write_text(json.dumps(stream, sort_keys=True) + "\n")
        s = stream["stats"]
        print("encode: faces %d -> %d, collapses %d, levels %d"
              % (s["faces"], s["base_faces"], s["collapses"],
                 len(stream["levels"])), file=sys.stderr)
        print("stop: %s" % s["stop"], file=sys.stderr)
        if s["unreachable"]:
            print("unreachable targets: %s" % (s["unreachable"],),
                  file=sys.stderr)
    else:
        stream = json.loads(a.stream.read_text())
        mesh = decode_mesh(stream, a.level)
        a.output.write_text(json.dumps(mesh, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
