"""Progressive triangle meshes: constrained, deterministic, exactly replayable LOD.

Encode performs edge collapses under hard constraints:
  * open boundary loops, material seams, UV-island seams and hard (sharp)
    edges are preserved as polylines -- collapses may only shorten them
    along their own direction, never break, merge or detach them;
  * the link condition is enforced, so no duplicate faces or non-manifold
    edges can appear;
  * any collapse that would flip or degenerate a face is rejected;
  * a collapse is only applied while its accumulated geometric error stays
    within max_error (per-vertex error spheres give a verifiable upper bound).

Every collapse is stored as an invertible vertex-split record keeping the
stable vertex/face IDs and the full per-corner attributes, so decoding any
level -- or stepping forward/backward between levels -- reproduces topology
and attributes exactly; the finest level is identical to the input mesh.

Determinism: candidates are ordered by (error, removed_id, kept_id) and all
traversals use sorted stable IDs, so permuting the input vertex/face arrays
does not change the normalized stream.
"""
import argparse
import copy
import heapq
import json
import math
import sys
from pathlib import Path

HARD_EDGE_COS = math.cos(math.radians(30.0))  # dihedral sharper than 30 deg => hard edge
EPS = 1e-9


# ---------------------------------------------------------------- small vec helpers
def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _len(a):
    return math.sqrt(_dot(a, a))


def _dist(a, b):
    return _len(_sub(a, b))


# ---------------------------------------------------------------- mesh state
class MeshState:
    """Mutable mesh with stable IDs; positions/uv kept exactly as given."""

    def __init__(self, mesh):
        verts = mesh["vertices"]
        faces = mesh["faces"]
        ids = [v["id"] for v in verts]
        fids = [f["id"] for f in faces]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate vertex ids")
        if len(set(fids)) != len(fids):
            raise ValueError("duplicate face ids")
        self.pos = {v["id"]: list(v["p"]) for v in verts}
        self.faces = {}
        for f in faces:
            if len(f["v"]) != 3 or len(f["uv"]) != 3:
                raise ValueError("faces must be triangles with 3 corner uvs")
            for vid in f["v"]:
                if vid not in self.pos:
                    raise ValueError("face references unknown vertex %r" % vid)
            self.faces[f["id"]] = {
                "id": f["id"],
                "v": list(f["v"]),
                "uv": [list(c) for c in f["uv"]],
                "material": f["material"],
            }
        self.radius = {vid: 0.0 for vid in self.pos}  # accumulated error sphere
        self._rebuild()

    def _rebuild(self):
        self.v2f = {vid: set() for vid in self.pos}
        self.adj = {vid: set() for vid in self.pos}
        self.edges = {}  # (lo,hi) -> [face_id, ...]
        for fid, f in self.faces.items():
            v = f["v"]
            for i in range(3):
                a, b = v[i], v[(i + 1) % 3]
                self.v2f[a].add(fid)
                self.adj[a].add(b)
                self.adj[b].add(a)
                key = (a, b) if a < b else (b, a)
                self.edges.setdefault(key, []).append(fid)

    # ---- feature classification -------------------------------------------
    def _normal(self, fid):
        p = [self.pos[v] for v in self.faces[fid]["v"]]
        return _cross(_sub(p[1], p[0]), _sub(p[2], p[0]))

    def edge_kind(self, a, b):
        """'boundary' | 'seam' (uv/material/hard) | 'smooth' | 'nonmanifold'."""
        key = (a, b) if a < b else (b, a)
        inc = self.edges.get(key, [])
        if len(inc) == 1:
            return "boundary"
        if len(inc) != 2:
            return "nonmanifold"
        f1, f2 = self.faces[inc[0]], self.faces[inc[1]]
        if f1["material"] != f2["material"]:
            return "seam"
        ca1, ca2 = f1["v"].index(a), f2["v"].index(a)
        cb1, cb2 = f1["v"].index(b), f2["v"].index(b)
        if f1["uv"][ca1] != f2["uv"][ca2] or f1["uv"][cb1] != f2["uv"][cb2]:
            return "seam"
        n1, n2 = self._normal(inc[0]), self._normal(inc[1])
        l1, l2 = _len(n1), _len(n2)
        if l1 > 0.0 and l2 > 0.0 and _dot(n1, n2) < HARD_EDGE_COS * l1 * l2:
            return "seam"  # hard edge: same protection class as attribute seams
        return "smooth"

    def feature_edges(self, v):
        out = []
        for o in sorted(self.adj[v]):
            k = self.edge_kind(v, o)
            if k in ("boundary", "seam"):
                out.append((k, o))
        return out

    # ---- collapse legality -------------------------------------------------
    def feature_pair_ok(self, x, y):
        """Attribute/boundary preservation rule for collapsing x into y."""
        key = (x, y) if x < y else (y, x)
        if key not in self.edges:
            return False
        if self.edge_kind(x, y) == "nonmanifold":
            return False
        fe = self.feature_edges(x)
        if len(fe) == 1:
            return False  # feature endpoints are anchors, never removed
        if len(fe) > 2:
            return False  # junctions are anchors
        if len(fe) == 2:
            if fe[0][0] != fe[1][0]:
                return False  # x sits on two different feature polylines
            kind = fe[0][0]
            if self.edge_kind(x, y) != kind:
                return False  # may only collapse along its own feature polyline
            for k2, _ in self.feature_edges(y):
                if k2 != kind:
                    return False  # would drag the feature onto a foreign vertex
        return True

    def legal(self, x, y):
        if not self.feature_pair_ok(x, y):
            return False
        # link condition: common neighbours == vertices opposite the edge
        key = (x, y) if x < y else (y, x)
        expected = set()
        for fid in self.edges[key]:
            for vid in self.faces[fid]["v"]:
                if vid != x and vid != y:
                    expected.add(vid)
        if (self.adj[x] & self.adj[y]) != expected:
            return False
        # orientation / degeneracy: no face may flip or collapse to zero area
        py = self.pos[y]
        for fid in sorted(self.v2f[x]):
            f = self.faces[fid]
            if y in f["v"]:
                continue
            vs = f["v"]
            pts = [self.pos[v] for v in vs]
            n_old = _cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0]))
            pts[vs.index(x)] = py
            n_new = _cross(_sub(pts[1], pts[0]), _sub(pts[2], pts[0]))
            l_old, l_new = _len(n_old), _len(n_new)
            if l_new <= 1e-12:
                return False
            if _dot(n_new, n_old) <= 1e-9 * l_old * l_new:
                return False
        return True

    # ---- the collapse itself ----------------------------------------------
    def collapse(self, x, y):
        """Remove x into y. Returns an invertible record (deep copies)."""
        px, py = self.pos[x], self.pos[y]
        inc = sorted(self.v2f[x])
        removed = [copy.deepcopy(self.faces[f]) for f in inc]
        modified = [f for f in inc if y not in self.faces[f]["v"]]
        mset = set(modified)
        for fid in modified:
            f = self.faces[fid]
            # corner keeps its slot, hence keeps its uv; material untouched
            f["v"][f["v"].index(x)] = y
        for fid in inc:
            if fid not in mset:
                del self.faces[fid]
        added = [copy.deepcopy(self.faces[f]) for f in modified]
        self.radius[y] = max(self.radius[y], self.radius[x] + _dist(px, py))
        del self.pos[x]
        del self.radius[x]
        self._rebuild()
        return {
            "kept": y,
            "vertex": {"id": x, "p": list(px)},
            "removed": removed,   # full records of every face that touched x
            "added": added,       # full records of the modified faces (same ids)
        }


# ---------------------------------------------------------------- encoding
def encode(mesh, targets, max_error):
    st = MeshState(mesh)
    orig_count = len(st.faces)
    targets = sorted({int(t) for t in targets}, reverse=True)  # coarsest crossed last
    collapses = []          # in collapse order
    marks = []              # (face_count, collapses_done, error_bound)
    ti = 0

    def max_radius():
        return max(st.radius.values()) if st.radius else 0.0

    def mark():
        nonlocal ti
        while ti < len(targets) and len(st.faces) <= targets[ti]:
            m = (len(st.faces), len(collapses))
            if not any(a == m[0] and b == m[1] for a, b, _ in marks):
                marks.append((len(st.faces), len(collapses), max_radius()))
            ti += 1

    def cost(x, y):
        return st.radius[x] + _dist(st.pos[x], st.pos[y])

    heap = []
    failed = set()

    def maybe_push(x, y):
        if x not in st.pos or y not in st.pos or (x, y) in failed:
            return
        c = cost(x, y)
        if c > max_error + EPS:
            return
        if not st.feature_pair_ok(x, y):
            return
        heapq.heappush(heap, (c, x, y))

    for x in sorted(st.pos):
        for y in sorted(st.adj[x]):
            maybe_push(x, y)
    mark()

    stopped = None
    while ti < len(targets):
        if not heap:
            stopped = _diagnose(st, max_error, cost)
            break
        c, x, y = heapq.heappop(heap)
        if x not in st.pos or y not in st.pos or y not in st.adj.get(x, ()):
            continue
        if (x, y) in failed:
            continue
        cc = cost(x, y)
        if cc > max_error + EPS:
            continue  # radii only grow: this pair is dead for good
        if abs(cc - c) > 1e-9:
            heapq.heappush(heap, (cc, x, y))  # stale entry, refresh
            continue
        if not st.legal(x, y):
            failed.add((x, y))
            continue
        rec = st.collapse(x, y)
        collapses.append(rec)
        mark()
        kept = rec["kept"]
        aff = set(st.adj.get(kept, ())) | {kept, x}
        for pair in list(failed):
            if pair[0] in aff or pair[1] in aff:
                failed.discard(pair)
        for u in sorted(aff):
            if u not in st.pos:
                continue
            for w in sorted(st.adj.get(u, ())):
                maybe_push(u, w)

    c_total = len(collapses)
    levels = [
        {"count": c, "split": c_total - s, "error": e}
        for (c, s, e) in reversed(marks)
    ]
    if not levels or levels[-1]["split"] != c_total:
        levels.append({"count": orig_count, "split": c_total, "error": 0.0})

    splits = [
        {
            "kept": r["kept"],
            "vertex": r["vertex"],
            "restore": r["removed"],
            "added": r["added"],
        }
        for r in reversed(collapses)
    ]
    stream = {
        "format": "progressive-mesh/1",
        "base": _canonical(st.pos, st.faces),
        "splits": splits,
        "levels": levels,
        "max_error": max_error,
        "stopped": stopped,
        "stats": {
            "collapses": c_total,
            "faces_original": orig_count,
            "faces_base": len(st.faces),
            "error_bound_base": max_radius(),
        },
    }
    return stream


def _diagnose(st, max_error, cost):
    best = None
    any_pair = False
    for x in sorted(st.pos):
        for y in sorted(st.adj[x]):
            if not st.feature_pair_ok(x, y):
                continue
            any_pair = True
            c = cost(x, y)
            if best is None or c < best:
                best = c
    head = "stopped at %d faces" % len(st.faces)
    if not any_pair:
        return head + ": no constraint-preserving collapse remains " \
            "(every remaining edge touches a feature endpoint/junction)"
    if best is not None and best > max_error + EPS:
        return head + ": cheapest constraint-preserving collapse would " \
            "introduce error %.6g > max_error %.6g" % (best, max_error)
    return head + ": remaining candidates all fail the link condition " \
        "or would flip/degenerate a face"


def _canonical(pos, faces):
    return {
        "vertices": [{"id": i, "p": list(pos[i])} for i in sorted(pos)],
        "faces": [copy.deepcopy(faces[i]) for i in sorted(faces)],
    }


# ---------------------------------------------------------------- decoding
def _apply_split(pos, faces, rec):
    pos[rec["vertex"]["id"]] = list(rec["vertex"]["p"])
    for f in rec["added"]:
        faces.pop(f["id"], None)
    for f in rec["restore"]:
        faces[f["id"]] = copy.deepcopy(f)


def _undo_split(pos, faces, rec):
    for f in rec["restore"]:
        faces.pop(f["id"], None)
    pos.pop(rec["vertex"]["id"], None)
    for f in rec["added"]:
        faces[f["id"]] = copy.deepcopy(f)


class Navigator:
    """Random-access + incremental forward/backward replay of a stream."""

    def __init__(self, stream):
        base = stream["base"]
        self.pos = {v["id"]: list(v["p"]) for v in base["vertices"]}
        self.faces = {f["id"]: copy.deepcopy(f) for f in base["faces"]}
        self.splits = stream["splits"]
        self.levels = stream["levels"]
        self.at = 0  # number of splits applied

    def goto_split(self, k):
        while self.at < k:
            _apply_split(self.pos, self.faces, self.splits[self.at])
            self.at += 1
        while self.at > k:
            self.at -= 1
            _undo_split(self.pos, self.faces, self.splits[self.at])

    def goto_level(self, k):
        self.goto_split(self.levels[k]["split"])

    def mesh(self):
        return _canonical(self.pos, self.faces)


def decode(stream, level):
    if not 0 <= level < len(stream["levels"]):
        raise ValueError("level %r out of range 0..%d"
                         % (level, len(stream["levels"]) - 1))
    nav = Navigator(stream)
    nav.goto_level(level)
    return nav.mesh()


# ---------------------------------------------------------------- CLI
def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="op", required=True)
    e = sub.add_parser("encode")
    e.add_argument("mesh", type=Path)
    e.add_argument("stream", type=Path)
    d = sub.add_parser("decode")
    d.add_argument("stream", type=Path)
    d.add_argument("output", type=Path)
    d.add_argument("--level", type=int, default=0)
    a = p.parse_args()

    if a.op == "encode":
        data = json.loads(a.mesh.read_text())
        stream = encode(data["mesh"], data.get("targets", []),
                        float(data.get("max_error", float("inf"))))
        a.stream.write_text(json.dumps(stream, sort_keys=True) + "\n")
        lv = ", ".join("L%d: %d faces (err<=%.4g)" % (i, l["count"], l["error"])
                       for i, l in enumerate(stream["levels"]))
        print("encoded %d collapses; levels: %s"
              % (stream["stats"]["collapses"], lv), file=sys.stderr)
        if stream["stopped"]:
            print("note: " + stream["stopped"], file=sys.stderr)
    else:
        stream = json.loads(a.stream.read_text())
        mesh = decode(stream, a.level)
        a.output.write_text(json.dumps(mesh, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
