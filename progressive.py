"""Progressive triangle mesh: constrained edge-collapse encoder and
vertex-split decoder.

encode: {mesh,targets,max_error} -> stream {base,splits,levels,diffs,
        checkpoints,stats}
decode: stream + zero-based level index -> canonical mesh {vertices,faces}

Guarantees
----------
* Boundary rings, material seams, UV-island seams and hard edges (dihedral
  angle above 30 degrees) are classified once on the input mesh.  A collapse
  may never remove a vertex incident to such a constrained edge, may never
  delete a face touching a constrained edge, and must pass the link
  condition, a duplicate-face test and a normal-flip test, so connected
  components, boundary loops, seams and face orientation are preserved at
  every level.
* Every vertex-split record stores the removed vertex (stable id + position)
  and the full original records of every face it touched, hence replaying
  all splits reproduces the input mesh exactly: topology and per-corner
  attributes match item by item under stable ids.
* Collapse candidates are ordered by (edge length, min id, max id) with a
  fixed direction preference, and all input arrays are sorted by id on
  load, so permuting the input vertex/face lists cannot change the
  canonical result.
* levels store split offsets into the flat coarse->fine split array and
  per-level checkpoints are attached, so a decoder may jump to a level and
  keep reading increments from there.
* If a target cannot be reached within max_error the encoder stops and
  reports an explicit reason in stats.stop.
"""
import argparse
import copy
import json
import math
import sys
from pathlib import Path

HARD_EDGE_COS = 0.8660254037844386  # cos(30 deg); larger dihedral => hard edge
ERR_TOL = 1e-12


# ---------------- vector helpers ----------------
def _sub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def _cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _dist(a, b):
    return math.sqrt(_dot(_sub(a, b), _sub(a, b)))


def _normal(p0, p1, p2):
    return _cross(_sub(p1, p0), _sub(p2, p0))


def _ekey(a, b):
    return (a, b) if a < b else (b, a)


# ---------------- mesh container ----------------
def canonical_face(f):
    return {"id": f["id"], "v": list(f["v"]),
            "uv": [list(c) for c in f["uv"]], "material": f["material"]}


def canonical_mesh(mesh):
    return {
        "vertices": [{"id": v, "p": list(mesh.verts[v])}
                     for v in sorted(mesh.verts)],
        "faces": [canonical_face(mesh.faces[f]) for f in sorted(mesh.faces)],
    }


class Mesh:
    def __init__(self, verts, faces):
        self.verts = verts  # vid -> [x,y,z]
        self.faces = faces  # fid -> face record
        self.rebuild()

    @classmethod
    def from_dict(cls, d):
        verts = {}
        for v in d["vertices"]:
            if v["id"] in verts:
                raise ValueError("duplicate vertex id %r" % v["id"])
            verts[v["id"]] = list(v["p"])
        faces = {}
        for f in d["faces"]:
            if f["id"] in faces:
                raise ValueError("duplicate face id %r" % f["id"])
            if len(set(f["v"])) != 3:
                raise ValueError("degenerate face id %r" % f["id"])
            for x in f["v"]:
                if x not in verts:
                    raise ValueError("face %r references unknown vertex %r"
                                     % (f["id"], x))
            faces[f["id"]] = {"id": f["id"], "v": list(f["v"]),
                              "uv": [list(c) for c in f["uv"]],
                              "material": f["material"]}
        return cls(verts, faces)

    def rebuild(self):
        self.v2f = {v: set() for v in self.verts}
        self.e2f = {}
        self.nbr = {v: set() for v in self.verts}
        for f in self.faces.values():
            vs = f["v"]
            for i in range(3):
                a, b = vs[i], vs[(i + 1) % 3]
                self.v2f[a].add(f["id"])
                self.e2f.setdefault(_ekey(a, b), []).append(f["id"])
                self.nbr[a].add(b)
                self.nbr[b].add(a)


def classify(mesh):
    """Return {edge: reason} for constrained edges of the given mesh.

    An edge is constrained when it is an open-boundary edge, a non-manifold
    edge, a material seam, a UV-island seam or a hard (crease) edge.
    """
    constrained = {}
    for e, fids in mesh.e2f.items():
        why = None
        if len(fids) == 1:
            why = "boundary"
        elif len(fids) > 2:
            why = "nonmanifold"
        else:
            f1, f2 = mesh.faces[fids[0]], mesh.faces[fids[1]]
            if f1["material"] != f2["material"]:
                why = "material"
            else:
                for x in e:
                    uv1 = f1["uv"][f1["v"].index(x)]
                    uv2 = f2["uv"][f2["v"].index(x)]
                    if uv1 != uv2:
                        why = "uv"
                        break
                if why is None:
                    n1 = _normal(*[mesh.verts[v] for v in f1["v"]])
                    n2 = _normal(*[mesh.verts[v] for v in f2["v"]])
                    l1, l2 = _dot(n1, n1), _dot(n2, n2)
                    if l1 > 0 and l2 > 0:
                        c = _dot(n1, n2) / math.sqrt(l1 * l2)
                        if c < HARD_EDGE_COS:
                            why = "hard"
        if why:
            constrained[e] = why
    return constrained


# ---------------- simplification ----------------
class Simplifier:
    def __init__(self, mesh, max_error):
        self.mesh = mesh
        self.pos = {v: list(p) for v, p in mesh.verts.items()}  # immutable
        self.max_error = max_error
        kinds = classify(mesh)
        self.constrained = set(kinds)
        cverts = set()
        for e in self.constrained:
            cverts.update(e)
        self.free = set(mesh.verts) - cverts
        # representative vertex -> original vertices it stands for
        self.clusters = {v: {v} for v in mesh.verts}
        self.bound = 0.0            # max |p_orig - p_representative|
        self.collapses = []         # split records, fine -> coarse order
        self.k_at = {}              # face count -> collapses performed
        self.snapshots = {}         # face count -> canonical mesh
        self.level_error = {}       # face count -> error bound

    def directions(self, x, y):
        """Ordered (kept, removed) attempts for edge (x,y) with x<y."""
        fx, fy = x in self.free, y in self.free
        if fx and fy:
            return [(x, y), (y, x)]  # prefer removing the larger id
        if fy:
            return [(x, y)]          # keep the constrained endpoint
        if fx:
            return [(y, x)]
        return []

    def evaluate(self, u, v):
        """Check collapse v -> u.  Returns (payload, reason); payload is
        None when the collapse is illegal or over the error budget."""
        m = self.mesh
        e = _ekey(u, v)
        if e in self.constrained:
            return None, "constrained"
        ef = m.e2f.get(e)
        if not ef or len(ef) != 2:
            return None, "topology"
        opp = []
        for fid in ef:
            o = [x for x in m.faces[fid]["v"] if x != u and x != v]
            if len(o) != 1:
                return None, "topology"
            opp.append(o[0])
        a, b = opp
        if a == b:
            return None, "topology"
        # faces deleted by the collapse must not touch constrained edges
        if _ekey(u, a) in self.constrained or _ekey(u, b) in self.constrained:
            return None, "constrained"
        # link condition: exactly the two opposite vertices shared
        if m.nbr[u] & m.nbr[v] != {a, b}:
            return None, "link"
        mod_ids = sorted(fid for fid in m.v2f[v] if fid not in ef)
        newv = {}
        for fid in mod_ids:
            vs = [u if x == v else x for x in m.faces[fid]["v"]]
            if len(set(vs)) != 3:
                return None, "topology"
            newv[fid] = vs
        existing = {tuple(sorted(f["v"])) for fid, f in m.faces.items()
                    if fid not in newv and fid not in ef}
        for vs in newv.values():
            if tuple(sorted(vs)) in existing:
                return None, "duplicate"
        for fid, vs in newv.items():
            n_old = _normal(*[self.pos[x] for x in m.faces[fid]["v"]])
            n_new = _normal(*[self.pos[x] for x in vs])
            if _dot(n_old, n_old) <= 0 or _dot(n_new, n_new) <= 0:
                return None, "degenerate"
            if _dot(n_old, n_new) <= 0:
                return None, "flip"
        pu = self.pos[u]
        for w in self.clusters[v]:
            if _dist(self.pos[w], pu) > self.max_error + ERR_TOL:
                return None, "error"
        return (sorted(ef), mod_ids, newv), "ok"

    def apply(self, u, v, payload):
        ef, mod_ids, newv = payload
        m = self.mesh
        rec = {"u": u,
               "v": {"id": v, "p": list(self.pos[v])},
               "add_faces": [canonical_face(m.faces[fid]) for fid in ef],
               "restore_faces": [canonical_face(m.faces[fid])
                                 for fid in mod_ids]}
        for fid in ef:
            del m.faces[fid]
        for fid in mod_ids:
            m.faces[fid]["v"] = list(newv[fid])  # corner uv stays in place
        del m.verts[v]
        self.clusters[u] |= self.clusters.pop(v)
        b = max(_dist(self.pos[w], self.pos[u]) for w in self.clusters[u])
        self.bound = max(self.bound, b)
        self.collapses.append(rec)
        m.rebuild()

    def diagnose(self, cands):
        if not cands:
            return {"reason": "no_candidates",
                    "detail": "no interior, unconstrained edge with a free "
                              "endpoint remains; further collapses would "
                              "break boundary/material/UV/hard-edge "
                              "constraints"}
        reasons = {}
        cheapest = None
        for cost, x, y in cands:
            for uu, vv in self.directions(x, y):
                _, r = self.evaluate(uu, vv)
                reasons[r] = reasons.get(r, 0) + 1
                if r == "error" and (cheapest is None or cost < cheapest):
                    cheapest = cost
        if set(reasons) == {"error"}:
            return {"reason": "error_budget",
                    "detail": "cheapest remaining collapse (edge length "
                              "%.6g) would push the error bound %.6g past "
                              "max_error %.6g"
                              % (cheapest, self.bound, self.max_error)}
        return {"reason": "topology",
                "detail": "all remaining candidate collapses are rejected "
                          "by topology/attribute constraints; rejection "
                          "counts: %s"
                          % json.dumps(reasons, sort_keys=True)}

    def run(self, targets):
        init = len(self.mesh.faces)
        tset = sorted(set(targets))
        if init in tset:
            self.k_at[init] = 0
            self.snapshots[init] = canonical_mesh(self.mesh)
            self.level_error[init] = 0.0
        stop = None
        while len(self.mesh.faces) > tset[0]:
            cands = []
            for e, fids in self.mesh.e2f.items():
                if len(fids) != 2 or e in self.constrained:
                    continue
                x, y = e
                if not self.directions(x, y):
                    continue
                cands.append((_dist(self.pos[x], self.pos[y]), x, y))
            cands.sort()
            applied = False
            for cost, x, y in cands:
                for uu, vv in self.directions(x, y):
                    payload, _ = self.evaluate(uu, vv)
                    if payload is not None:
                        self.apply(uu, vv, payload)
                        applied = True
                        break
                if applied:
                    break
            if not applied:
                stop = self.diagnose(cands)
                break
            nf = len(self.mesh.faces)
            if nf in tset and nf not in self.k_at:
                self.k_at[nf] = len(self.collapses)
                self.snapshots[nf] = canonical_mesh(self.mesh)
                self.level_error[nf] = self.bound
        if stop is None:
            missed = [t for t in tset if t not in self.k_at]
            if missed:
                stop = {"reason": "target_missed",
                        "detail": "face-count targets not reachable exactly "
                                  "(a collapse removes 2 faces at a time): "
                                  "%s" % missed}
            else:
                stop = {"reason": "ok", "detail": "all targets reached"}
        return tset, stop


def encode(data, seed=0):
    """Build the progressive stream from {mesh,targets,max_error}."""
    mesh = Mesh.from_dict(data["mesh"])
    init_faces = len(mesh.faces)
    targets = data.get("targets")
    if not targets:
        raise ValueError("input must provide non-empty 'targets'")
    for t in targets:
        if not isinstance(t, int) or t <= 0 or t > init_faces:
            raise ValueError("invalid target face count %r" % (t,))
    max_error = data.get("max_error")
    if max_error is None:
        max_error = float("inf")

    sim = Simplifier(mesh, max_error)
    tset, stop = sim.run(targets)

    n = len(sim.collapses)
    splits = list(reversed(sim.collapses))  # coarse -> fine
    levels = []
    for t in tset:
        if t not in sim.k_at:
            continue
        k = sim.k_at[t]
        levels.append({"count": t, "split": n - k,
                       "error": sim.level_error[t]})
    levels.sort(key=lambda L: L["count"])
    base_count = len(mesh.faces)
    if not levels or levels[0]["count"] != base_count:
        levels.insert(0, {"count": base_count, "split": 0, "error": sim.bound})
    base = sim.snapshots.get(base_count) or canonical_mesh(mesh)

    checkpoints = {}
    for i, L in enumerate(levels):
        if i == 0:
            continue  # level 0 is the base mesh itself
        snap = sim.snapshots.get(L["count"])
        if snap is not None:
            checkpoints[str(i)] = snap

    diffs = []
    for i in range(len(levels) - 1):
        a, b = levels[i]["split"], levels[i + 1]["split"]
        diffs.append({"from_level": i, "to_level": i + 1,
                      "split_start": a, "split_end": b,
                      "splits": copy.deepcopy(splits[a:b])})

    return {
        "format": "pmesh/1",
        "base": base,
        "splits": splits,
        "levels": levels,
        "diffs": diffs,
        "checkpoints": checkpoints,
        "stats": {
            "seed": seed,
            "targets": tset,
            "max_error": (None if max_error == float("inf") else max_error),
            "collapses": n,
            "stop": stop,
        },
    }


# ---------------- decoding ----------------
def apply_split(mesh, s):
    mesh.verts[s["v"]["id"]] = list(s["v"]["p"])
    for f in s["restore_faces"]:
        mesh.faces[f["id"]] = canonical_face(f)
    for f in s["add_faces"]:
        mesh.faces[f["id"]] = canonical_face(f)


def decode(stream, level, use_checkpoints=True):
    levels = stream["levels"]
    if not 0 <= level < len(levels):
        raise ValueError("level %r out of range [0,%d)"
                         % (level, len(levels)))
    start_split = 0
    mesh = Mesh.from_dict(stream["base"])
    if use_checkpoints:
        best = None
        for k in stream.get("checkpoints", {}):
            ki = int(k)
            if ki <= level and (best is None or ki > best):
                best = ki
        if best is not None:
            mesh = Mesh.from_dict(stream["checkpoints"][str(best)])
            start_split = levels[best]["split"]
    for s in stream["splits"][start_split:levels[level]["split"]]:
        apply_split(mesh, s)
    mesh.rebuild()
    if len(mesh.faces) != levels[level]["count"]:
        raise ValueError("level %d declares %d faces but decode produced %d"
                         % (level, levels[level]["count"], len(mesh.faces)))
    return canonical_mesh(mesh)


# ---------------- CLI ----------------
def main():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="op", required=True)
    e = sub.add_parser("encode")
    e.add_argument("mesh", type=Path)
    e.add_argument("stream", type=Path)
    e.add_argument("--seed", type=int, default=0,
                   help="recorded for reproducibility; encoding is "
                        "deterministic and does not depend on it")
    d = sub.add_parser("decode")
    d.add_argument("stream", type=Path)
    d.add_argument("output", type=Path)
    d.add_argument("--level", type=int, default=0)
    a = p.parse_args()

    if a.op == "encode":
        data = json.loads(a.mesh.read_text())
        stream = encode(data, seed=a.seed)
        a.stream.write_text(json.dumps(stream, sort_keys=True) + "\n")
        st = stream["stats"]
        print("levels: %s" % [(L["count"], L["split"])
                              for L in stream["levels"]])
        print("collapses: %d  stop: %s (%s)"
              % (st["collapses"], st["stop"]["reason"],
                 st["stop"]["detail"]))
        if st["stop"]["reason"] != "ok":
            print("warning: simplification stopped early: %s"
                  % st["stop"]["detail"], file=sys.stderr)
    else:
        stream = json.loads(a.stream.read_text())
        try:
            result = decode(stream, a.level)
        except ValueError as exc:
            print("decode error: %s" % exc, file=sys.stderr)
            sys.exit(2)
        a.output.write_text(json.dumps(result, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
