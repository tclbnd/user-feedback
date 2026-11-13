#!/usr/bin/env python3
"""Constrained progressive triangle meshes.

Edge-collapse encoding with replayable vertex-split records.

Hard constraints (a collapse is rejected when any one fails):
  * material seam  - the collapsed edge may not sit between different
    materials;
  * UV island seam - per-corner UVs at both edge endpoints must match
    across the two incident faces;
  * hard edge      - boundary edges, non-manifold edges and edges whose
    incident face normals form a dihedral angle above HARD_ANGLE are
    never collapsed (explicit ``hard_edges`` in the input are honoured);
  * open boundary  - vertices on a boundary loop never participate, so
    boundary loops are preserved vertex for vertex;
  * topology       - the simplicial link condition must hold and the
    collapse must not create duplicate faces;
  * orientation    - every surviving triangle must keep its winding
    (no flips, no degenerate triangles);
  * error budget   - both the quadric error and the maximum displacement
    of any represented original vertex stay within ``max_error``.

All identifiers are stable: original vertex/face IDs are never reused,
every collapse stores an inverse vertex-split record, and tie ordering
between equal-error candidates is fully determined by IDs, so shuffling
the input lists cannot change the normalised result.
"""
from __future__ import annotations

import argparse
import heapq
import json
import math
from collections import defaultdict
from pathlib import Path

HARD_ANGLE = math.radians(30.0)      # dihedral threshold for implicit hard edges
DET_EPS = 1e-12                      # quadric matrix singularity threshold
FLIP_EPS = 1e-15                     # oriented area / normal dot threshold


# --------------------------------------------------------------------------- #
# vector helpers
# --------------------------------------------------------------------------- #
def vsub(a, b):
    return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]


def vdot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def vcross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def vdist2(a, b):
    d = vsub(a, b)
    return vdot(d, d)


def face_normal(points):
    return vcross(vsub(points[1], points[0]), vsub(points[2], points[0]))


# --------------------------------------------------------------------------- #
# symmetric 4x4 quadric, stored as 10 coefficients
# Q = (a b c d / b e f g / c f h i / d g i j)
# --------------------------------------------------------------------------- #
def q_zero():
    return [0.0] * 10


def q_add(q, r):
    return [q[k] + r[k] for k in range(10)]


def q_plane(normal, point):
    a, b, c = normal
    length = math.sqrt(a * a + b * b + c * c)
    if length <= 0.0:
        return q_zero()
    a, b, c = a / length, b / length, c / length
    d = -(a * point[0] + b * point[1] + c * point[2])
    k = (a, b, c, d)
    idx = ((0, 0), (0, 1), (0, 2), (0, 3), (1, 1),
           (1, 2), (1, 3), (2, 2), (2, 3), (3, 3))
    return [k[i] * k[j] for i, j in idx]


def q_eval(q, p):
    x, y, z = p
    a, b, c, d, e, f, g, h, i, j = q
    return (a * x * x + e * y * y + h * z * z
            + 2.0 * (b * x * y + c * x * z + f * y * z)
            + 2.0 * (d * x + g * y + i * z) + j)


def q_optimal_point(q):
    """Minimiser of the quadric; None when the 3x3 block is singular."""
    a, b, c = q[0], q[1], q[2]
    e, f, g = q[4], q[5], q[6]
    h, i = q[7], q[8]
    d = q[3]
    mat = ((a, b, c), (b, e, f), (c, f, h))
    det = (mat[0][0] * (mat[1][1] * mat[2][2] - mat[1][2] * mat[2][1])
           - mat[0][1] * (mat[1][0] * mat[2][2] - mat[1][2] * mat[2][0])
           + mat[0][2] * (mat[1][0] * mat[2][1] - mat[1][1] * mat[2][0]))
    scale = max(1.0, abs(a), abs(e), abs(h)) ** 3
    if abs(det) <= DET_EPS * scale:
        return None

    def cramer(col):
        m = [list(row) for row in mat]
        for r in range(3):
            m[r][col] = (-d, -g, -i)[r]
        return ((m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
                 - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
                 + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])) / det)

    return [cramer(0), cramer(1), cramer(2)]


# --------------------------------------------------------------------------- #
# mesh state used while encoding
# --------------------------------------------------------------------------- #
class MeshState:
    def __init__(self, mesh):
        self.p = {}                       # vid -> [x, y, z]
        self.faces = {}                   # fid -> dict(v, uv, material)
        for v in mesh["vertices"]:
            self.p[v["id"]] = [float(c) for c in v["p"]]
        for f in mesh["faces"]:
            if len(f["v"]) != 3 or len(f["uv"]) != 3:
                raise ValueError("only triangle faces with per-corner uv")
            self.faces[f["id"]] = {
                "v": tuple(f["v"]),
                "uv": tuple(tuple(float(c) for c in uv) for uv in f["uv"]),
                "material": f["material"],
            }
        self._rebuild_topology()
        self._classify_edges(mesh.get("hard_edges", ()))
        self.quadric = {vid: q_zero() for vid in self.p}
        for fid in sorted(self.faces):
            face = self.faces[fid]
            pts = [self.p[vid] for vid in face["v"]]
            q = q_plane(face_normal(pts), pts[0])
            for vid in face["v"]:
                self.quadric[vid] = q_add(self.quadric[vid], q)
        self.rejections = defaultdict(int)

    # --------------------------------------------------------------- topology
    def _rebuild_topology(self):
        self.vfaces = defaultdict(set)
        self.edge_faces = defaultdict(set)
        for fid in sorted(self.faces):
            verts = self.faces[fid]["v"]
            for vid in verts:
                self.vfaces[vid].add(fid)
            for k in range(3):
                edge = tuple(sorted((verts[k], verts[(k + 1) % 3])))
                self.edge_faces[edge].add(fid)
        self.boundary_verts = set()
        for edge, fids in self.edge_faces.items():
            if len(fids) == 1:
                self.boundary_verts.update(edge)

    def _classify_edges(self, explicit_hard):
        self.hard_edges = set(tuple(sorted(e)) for e in explicit_hard)
        self.seam_uv = set()
        self.seam_material = set()
        for edge in sorted(self.edge_faces):
            self._classify_one(edge)

    def _classify_one(self, edge):
        """(Re)classify a single edge against the current geometry.

        New edges created by a collapse are re-evaluated here so a fold can
        never be flattened later, and rerouted faces cannot silently turn an
        interior edge into a material or UV seam.
        """
        fids = sorted(self.edge_faces.get(edge, ()))
        self.hard_edges.discard(edge)
        self.seam_uv.discard(edge)
        self.seam_material.discard(edge)
        if len(fids) != 2:
            self.hard_edges.add(edge)            # boundary / non-manifold
            return
        f0, f1 = self.faces[fids[0]], self.faces[fids[1]]
        if f0["material"] != f1["material"]:
            self.seam_material.add(edge)
            self.hard_edges.add(edge)
        # per-corner UV comparison at both endpoints
        uv0 = {f0["v"][k]: f0["uv"][k] for k in range(3)}
        uv1 = {f1["v"][k]: f1["uv"][k] for k in range(3)}
        if any(uv0[vid] != uv1[vid] for vid in edge):
            self.seam_uv.add(edge)
            self.hard_edges.add(edge)
        n0 = face_normal([self.p[v] for v in f0["v"]])
        n1 = face_normal([self.p[v] for v in f1["v"]])
        l0, l1 = math.sqrt(vdot(n0, n0)), math.sqrt(vdot(n1, n1))
        if l0 > 0.0 and l1 > 0.0:
            cos_a = vdot(n0, n1) / (l0 * l1)
            if cos_a < math.cos(HARD_ANGLE):
                self.hard_edges.add(edge)

    def _reclassify_around(self, vid):
        seen = set()
        for fid in self.vfaces[vid]:
            for k in range(3):
                edge = tuple(sorted(
                    (self.faces[fid]["v"][k],
                     self.faces[fid]["v"][(k + 1) % 3])))
                if edge not in seen:
                    seen.add(edge)
                    self._classify_one(edge)

    def neighbours(self, vid):
        out = set()
        for fid in self.vfaces[vid]:
            for other in self.faces[fid]["v"]:
                if other != vid:
                    out.add(other)
        return out

    # ------------------------------------------------------------- legality #
    def check_collapse(self, u, v, x):
        """Return (ok, reason).  v is the surviving vertex."""
        edge = tuple(sorted((u, v)))
        if edge in self.hard_edges:
            if edge in self.seam_material:
                return False, "material_seam"
            if edge in self.seam_uv:
                return False, "uv_seam"
            if len(self.edge_faces[edge]) == 1:
                return False, "boundary_edge"
            if len(self.edge_faces[edge]) > 2:
                return False, "nonmanifold_edge"
            return False, "hard_edge"
        if u in self.boundary_verts or v in self.boundary_verts:
            return False, "boundary_vertex"

        # simplicial link condition (an interior edge has exactly two faces)
        edge_fids = self.edge_faces[edge]
        if len(edge_fids) != 2:
            return False, "nonmanifold_edge"
        opposite = set()
        for fid in edge_fids:
            for w in self.faces[fid]["v"]:
                if w != u and w != v:
                    opposite.add(w)
        if self.neighbours(u) & self.neighbours(v) != opposite:
            return False, "link_condition"

        affected = self.vfaces[u] | self.vfaces[v]
        old_normals, new_triples, face_order = {}, {}, []
        for fid in sorted(affected):
            verts = self.faces[fid]["v"]
            if u in verts and v in verts:
                continue                                # removed edge faces
            old_normals[fid] = face_normal([self.p[w] for w in verts])
            face_order.append(fid)
            new_triples[fid] = tuple(v if w == u else w for w in verts)

        # duplicate / degenerate faces after remap
        seen = {}
        for fid in sorted(self.faces):
            if fid in edge_fids:
                continue
            verts = self.faces[fid]["v"]
            triple = tuple(v if w == u else w for w in verts)
            if len(set(triple)) < 3:
                return False, "degenerate_face"
            key = tuple(sorted(triple))
            if key in seen:
                return False, "duplicate_face"
            seen[key] = fid

        # simulate the post-collapse edge classification: only *new* edges
        # (edges that did not exist before the collapse) can change status,
        # so they alone must not introduce a material seam, UV seam or fold.
        # Pre-existing seams are already locked by self.hard_edges.
        simulated_face = {}
        for fid in sorted(self.faces):
            if fid in edge_fids:
                continue
            face = self.faces[fid]
            verts = tuple(v if w == u else w for w in face["v"])
            simulated_face[fid] = (verts, face["uv"], face["material"])
        simulated = defaultdict(list)
        for fid, (verts, _, _) in simulated_face.items():
            for k in range(3):
                e = tuple(sorted((verts[k], verts[(k + 1) % 3])))
                simulated[e].append(fid)
        for e, fid_list in simulated.items():
            if len(fid_list) != 2 or e in self.edge_faces:
                continue
            (v0, uv0, m0), (v1, uv1, m1) = (simulated_face[fid_list[0]],
                                            simulated_face[fid_list[1]])
            if m0 != m1:
                return False, "material_seam"
            corner0 = {v0[k]: uv0[k] for k in range(3)}
            corner1 = {v1[k]: uv1[k] for k in range(3)}
            if any(corner0[w] != corner1[w] for w in e):
                return False, "uv_seam"
            p0 = [x if w == v else self.p[w] for w in v0]
            p1 = [x if w == v else self.p[w] for w in v1]
            n0, n1 = face_normal(p0), face_normal(p1)
            l0, l1 = math.sqrt(vdot(n0, n0)), math.sqrt(vdot(n1, n1))
            if l0 > 0.0 and l1 > 0.0:
                cos_a = vdot(n0, n1) / (l0 * l1)
                if cos_a < math.cos(HARD_ANGLE):
                    return False, "hard_edge"

        # orientation / flip test
        for fid in face_order:
            mapped = new_triples[fid]
            new_n = face_normal([x if w == v else self.p[w] for w in mapped])
            if vdot(new_n, new_n) <= FLIP_EPS:
                return False, "degenerate_face"
            if vdot(old_normals[fid], new_n) <= FLIP_EPS:
                return False, "face_flip"
        return True, "ok"

    # ------------------------------------------------------------- execution #
    def collapse(self, u, v, x):
        """Collapse u into v at position x; return the inverse split record."""
        edge = tuple(sorted((u, v)))
        edge_fids = set(self.edge_faces[edge])
        deleted, reroute = [], []
        for fid in sorted(edge_fids):
            face = self.faces[fid]
            deleted.append({
                "id": fid,
                "v": list(face["v"]),
                "uv": [list(uv) for uv in face["uv"]],
                "material": face["material"],
            })
        for fid in sorted(self.vfaces[u]):
            if fid in edge_fids:
                continue
            face = self.faces[fid]
            old_v = tuple(face["v"])
            idx = old_v.index(u)
            new_v = list(old_v)
            new_v[idx] = v
            # full self-contained snapshot: this face may be deleted by a
            # later collapse, and a split replay must not need it on hand
            reroute.append({
                "id": fid,
                "old": {"v": list(old_v),
                        "uv": [list(uv) for uv in face["uv"]],
                        "material": face["material"]},
                "new_v": new_v,
            })
            face["v"] = tuple(new_v)

        record = {
            "edge": list(edge),
            "removed": u,
            "kept": v,
            "removed_p": list(self.p[u]),
            "kept_old_p": list(self.p[v]),
            "kept_new_p": list(x),
            "deleted_faces": deleted,
            "reroute": reroute,
        }
        for fid in edge_fids:
            del self.faces[fid]
        self.p[v] = list(x)
        del self.p[u]
        self.quadric[v] = q_add(self.quadric[v], self.quadric[u])
        del self.quadric[u]
        self._rebuild_topology()
        self._reclassify_around(v)
        return record


# --------------------------------------------------------------------------- #
# encoding
# --------------------------------------------------------------------------- #
def _candidate(state, edge, max_error):
    """Build the heap entry for one edge or None when it cannot be offered.

    Every geometrically plausible target point is tested against the error
    budget and the legality constraints; the cheapest surviving one wins.
    Tie order is fixed (quadric error, option rank, removed id, kept id),
    so two runs over a permuted input produce identical heap entries.
    """
    u, v = edge
    if u not in state.p or v not in state.p:
        return None
    q = q_add(state.quadric[u], state.quadric[v])
    pu, pv = state.p[u], state.p[v]
    opt = q_optimal_point(q)
    options = []
    if opt is not None:
        options.append((0, opt))
    options.append((1, [(pu[k] + pv[k]) * 0.5 for k in range(3)]))
    options.append((2, pu))
    options.append((3, pv))
    best = None
    for rank, pos in options:
        err = max(0.0, q_eval(q, pos))
        if math.sqrt(err) > max_error:
            continue
        ok, _ = state.check_collapse(u, v, pos)
        survivor, collapsed = v, u
        if not ok:
            ok2, _ = state.check_collapse(v, u, pos)
            if not ok2:
                continue
            survivor, collapsed = u, v
        entry = (err, rank, collapsed, survivor, tuple(pos))
        if best is None or entry < best:
            best = entry
    if best is None:
        return None
    err, rank, removed, kept, pos = best
    return (err, rank, removed, kept, list(pos))


def encode(mesh_doc):
    mesh = mesh_doc["mesh"]
    targets = sorted(int(t) for t in mesh_doc["targets"])
    max_error = float(mesh_doc["max_error"])
    state = MeshState(mesh)
    initial_faces = len(state.faces)
    initial_verts = len(state.p)

    checkpoints = [(initial_faces, 0)]          # (face count, split count)
    # coarsening reduces the face count, so the smallest target comes first
    pending = sorted(set(targets))

    heap = []
    for edge in sorted(state.edge_faces):
        entry = _candidate(state, edge, max_error)
        if entry is not None:
            heapq.heappush(heap, entry)

    splits, stop_reason = [], None
    while pending:
        if len(state.faces) == pending[-1]:
            checkpoints.append((len(state.faces), len(splits)))
            pending.pop()
            continue
        entry = None
        while heap:
            cand = heapq.heappop(heap)
            err, rank, u, v, pos = cand
            edge = tuple(sorted((u, v)))
            if u not in state.p or v not in state.p or edge not in state.edge_faces:
                continue
            fresh = _candidate(state, edge, max_error)
            if fresh is None:
                ok, reason = state.check_collapse(u, v, list(pos))
                state.rejections[reason if not ok else "error_budget"] += 1
                continue
            if fresh != cand:
                heapq.heappush(heap, fresh)
                continue
            entry = cand
            break
        if entry is None:
            stop_reason = _stop_reason(state, max_error)
            break
        err, rank, u, v, pos = entry
        ok, reason = state.check_collapse(u, v, list(pos))
        if not ok:
            state.rejections[reason] += 1
            continue
        record = state.collapse(u, v, list(pos))
        record["seq"] = len(splits)
        record["error"] = err
        splits.append(record)
        touched = set()
        for w in state.neighbours(v):
            touched.add(tuple(sorted((v, w))))
        for edge in sorted(touched):
            new_entry = _candidate(state, edge, max_error)
            if new_entry is not None:
                heapq.heappush(heap, new_entry)

    # checkpoints captured during coarsening as (face count, collapse count);
    # the original mesh is always reachable again as the finest level
    checkpoints.append((initial_faces, 0))
    if pending:
        checkpoints.append((len(state.faces), len(splits)))
    total_collapses = len(splits)

    # the stored stream is a coarse -> fine vertex-split stream, i.e. the
    # inverse of the recorded collapse order
    split_stream = list(reversed(splits))
    for idx, rec in enumerate(split_stream):
        rec["split_seq"] = idx

    # a state reached after c collapses needs (total-c) splits to rebuild
    cp = sorted({(count, total_collapses - collapses)
                 for count, collapses in checkpoints},
                key=lambda item: item[1])
    levels = [{"count": count, "split": need} for count, need in cp]
    base = _materialize(mesh, splits[:total_collapses])
    full = {"vertices": [{"id": v["id"], "p": [float(c) for c in v["p"]]}
                         for v in sorted(mesh["vertices"],
                                         key=lambda item: item["id"])],
            "faces": [{"id": f["id"], "v": list(f["v"]),
                       "uv": [[float(c) for c in uv] for uv in f["uv"]],
                       "material": f["material"]}
                      for f in sorted(mesh["faces"],
                                      key=lambda item: item["id"])]}

    diffs = []
    for k in range(len(levels) - 1):
        lo, hi = levels[k]["split"], levels[k + 1]["split"]
        diffs.append({"from_level": k, "to_level": k + 1,
                      "from_split": lo, "to_split": hi,
                      "splits": split_stream[lo:hi]})

    return {
        "base": base,
        "splits": split_stream,
        "levels": levels,
        "diffs": diffs,
        "full_restore": full,
        "stats": {
            "initial_vertices": initial_verts,
            "initial_faces": initial_faces,
            "base_vertices": len(base["vertices"]),
            "base_faces": len(base["faces"]),
            "collapse_count": len(splits),
            "rejection_counts": dict(sorted(state.rejections.items())),
            "hard_edges": sorted([list(e) for e in state.hard_edges]),
            "boundary_vertices": sorted(state.boundary_verts),
            "max_error_requested": max_error,
            "targets": targets,
            "targets_pending": sorted(pending),
        },
        "stop": None if not pending else {
            "stopped": True,
            "reason": stop_reason,
            "remaining_targets": sorted(pending),
            "face_count": len(state.faces),
        },
    }


def _stop_reason(state, max_error):
    reasons = ", ".join(f"{k}={v}" for k, v in
                        sorted(state.rejections.items())) or "none"
    return ("no legal collapsible edge remains within error budget "
            + str(max_error) + "; rejection counts: " + reasons)


# --------------------------------------------------------------------------- #
# decoding
# --------------------------------------------------------------------------- #
def _materialize(mesh, splits):
    p = {v["id"]: list(v["p"]) for v in mesh["vertices"]}
    faces = {f["id"]: {"v": list(f["v"]),
                       "uv": [list(uv) for uv in f["uv"]],
                       "material": f["material"]}
             for f in mesh["faces"]}
    for rec in splits:                       # forward: collapse u into v
        u, v = rec["removed"], rec["kept"]
        for r in rec["reroute"]:
            faces[r["id"]]["v"] = list(r["new_v"])
        for df in rec["deleted_faces"]:
            del faces[df["id"]]
        p[v] = list(rec["kept_new_p"])
        del p[u]
    return _export(p, faces)

def decode_stream(stream, level):
    levels = stream["levels"]
    if not 0 <= level < len(levels):
        raise IndexError("level " + str(level) + " out of range 0.."
                         + str(len(levels) - 1))
    return apply_splits(stream["base"], stream["splits"][:levels[level]["split"]])


def apply_splits(base_mesh, splits):
    p = {v["id"]: list(v["p"]) for v in base_mesh["vertices"]}
    faces = {f["id"]: {"v": list(f["v"]),
                       "uv": [list(uv) for uv in f["uv"]],
                       "material": f["material"]}
             for f in base_mesh["faces"]}
    for rec in splits:                       # base is coarse: split kept->removed
        kept, removed = rec["kept"], rec["removed"]
        for r in rec["reroute"]:
            fid = r["id"]
            if fid in faces:
                faces[fid]["v"] = list(r["old"]["v"])
            else:
                # the face was removed by a later (already undone) collapse
                faces[fid] = {
                    "v": list(r["old"]["v"]),
                    "uv": [list(uv) for uv in r["old"]["uv"]],
                    "material": r["old"]["material"],
                }
        for df in rec["deleted_faces"]:
            faces[df["id"]] = {
                "v": list(df["v"]),
                "uv": [list(uv) for uv in df["uv"]],
                "material": df["material"],
            }
        p[kept] = list(rec["kept_old_p"])
        p[removed] = list(rec["removed_p"])
    return _export(p, faces)


def apply_split(mesh, rec):
    """Apply one coarse -> fine vertex-split record to a mesh in place."""
    return apply_splits(mesh, [rec])


def apply_inverse_split(mesh, rec):
    """Undo one split record (replay the original edge collapse).

    Together with :func:`apply_split` this gives bidirectional single-step
    playback; every intermediate state still equals a prefix decode.
    """
    p = {v["id"]: list(v["p"]) for v in mesh["vertices"]}
    faces = {f["id"]: {"v": list(f["v"]),
                       "uv": [list(uv) for uv in f["uv"]],
                       "material": f["material"]}
             for f in mesh["faces"]}
    u, v = rec["removed"], rec["kept"]
    for r in rec["reroute"]:
        faces[r["id"]]["v"] = list(r["new_v"])
    for df in rec["deleted_faces"]:
        del faces[df["id"]]
    p[v] = list(rec["kept_new_p"])
    del p[u]
    return _export(p, faces)


def _export(p, faces):
    return {
        "vertices": [{"id": vid, "p": p[vid]} for vid in sorted(p)],
        "faces": [{"id": fid,
                   "v": list(faces[fid]["v"]),
                   "uv": [list(uv) for uv in faces[fid]["uv"]],
                   "material": faces[fid]["material"]}
                  for fid in sorted(faces)],
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="op", required=True)
    e = sub.add_parser("encode", help="encode a mesh document into a stream")
    e.add_argument("mesh", type=Path)
    e.add_argument("stream", type=Path)
    d = sub.add_parser("decode", help="decode one level of a stream")
    d.add_argument("stream", type=Path)
    d.add_argument("output", type=Path)
    d.add_argument("--level", type=int, default=0)
    diff = sub.add_parser("diff", help="write level meshes and adjacent diffs")
    diff.add_argument("stream", type=Path)
    diff.add_argument("prefix", type=Path)
    a = ap.parse_args()

    if a.op == "encode":
        doc = json.loads(a.mesh.read_text())
        stream = encode(doc)
        a.stream.write_text(json.dumps(stream, sort_keys=True) + "\n")
        return
    stream = json.loads(a.stream.read_text())
    if a.op == "decode":
        mesh = decode_stream(stream, a.level)
        a.output.write_text(json.dumps(mesh, sort_keys=True) + "\n")
    else:
        a.prefix.parent.mkdir(parents=True, exist_ok=True)
        for k in range(len(stream["levels"])):
            mesh = decode_stream(stream, k)
            Path(str(a.prefix) + ".level" + str(k) + ".json").write_text(
                json.dumps(mesh, sort_keys=True) + "\n")
        for k, chunk in enumerate(stream["diffs"]):
            Path(str(a.prefix) + ".diff" + str(k) + ".json").write_text(
                json.dumps(chunk, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
