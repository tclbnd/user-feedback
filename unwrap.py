#!/usr/bin/env python3
"""Seam-aware conformal (LSCM) unwrap with a minimal cut set.

    python3 unwrap.py mesh.json output.json

Input   {"pos": [[x,y,z], ...], "tris": [[a,b,c], ...]}
Output  {"uv":       [[u,v], ...]    one UV per input vertex
         "seams":    [[a,b], ...]    original-index edge pairs that were cut
         "seam_paths": [[v,...], ...] ordered seam polylines
         "islands":  n               number of UV islands (all topological disks)
         "island_faces": [[t,...]]   input triangle index per island
         "energy":   v               normalised conformal (LSCM) residual
         "angle_err": v              worst per-corner angle error, radians
         "area_dist": v              worst scale-invariant per-face area distortion
         "severed":  [[v,u,w], ...]  extra UV copies of cut vertices
         "digest":   "..."}          order-independent hash of the unwrap

Pipeline
--------
1. Canonicalise  - weld by position, renumber by geometry, drop degenerate
   triangles, make orientation consistent, sort faces canonically.  Every later
   decision uses this numbering, so nothing can depend on the input order.
2. Cut to disks  - build the minimal (irredundant) seam set that turns every
   connected component into a disk: one shortest non-separating cycle per genus
   handle, and one shortest path per extra pair of boundary loops.
3. Conformal solve - flatten each disk with LSCM; pins are chosen canonically
   (farthest boundary pair), which fixes the similarity gauge exactly.
4. Report        - seams, paths, per-vertex UVs, islands, energy statistics and
   a digest that is invariant under vertex / triangle reordering.

Topology model
--------------
A seam is modelled by splitting *corners* (face-vertex incidences).  The chart
of an island is the union-find of corners glued across every non-seam edge.  A
chart is a disk iff  chi = V_chart - E_chart + F = 1  and it has exactly one
boundary loop; both are measured directly, so the cut is verified rather than
assumed.  Any seam edge whose removal would restore disk-ness is provably
redundant, so an irredundant seam set is a local minimum in edge count.
"""
import hashlib
import heapq
import json
import math
import sys
from collections import defaultdict

EPS = 1e-12


# ==========================================================================
# vector helpers
# ==========================================================================
def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def cross(u, v):
    return (u[1] * v[2] - u[2] * v[1],
            u[2] * v[0] - u[0] * v[2],
            u[0] * v[1] - u[1] * v[0])


def dot(u, v):
    return u[0] * v[0] + u[1] * v[1] + u[2] * v[2]


def norm(u):
    return math.sqrt(dot(u, u))


def tri_area3(p, q, r):
    return 0.5 * norm(cross(sub(q, p), sub(r, p)))


def angle_at(p, q, r):
    u, v = sub(q, p), sub(r, p)
    lu, lv = norm(u), norm(v)
    if lu < 1e-14 or lv < 1e-14:
        return 0.0
    t = max(-1.0, min(1.0, dot(u, v) / (lu * lv)))
    return math.acos(t)


class UF:
    def __init__(self, n):
        self.p = list(range(n))

    def find(self, x):
        p = self.p
        while p[x] != x:
            p[x] = p[p[x]]
            x = p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra == rb:
            return False
        if ra < rb:
            self.p[rb] = ra
        else:
            self.p[ra] = rb
        return True


def ekey(u, v):
    return (u, v) if u < v else (v, u)


# ==========================================================================
# 1. canonical mesh
# ==========================================================================
class Canonical:
    """Mesh welded and renumbered by a purely geometric rule."""

    def __init__(self, pos, tris):
        self.input_pos = pos
        self.n_input = len(pos)

        # -- weld coincident vertices -------------------------------------
        q = 1e-9
        bucket = {}
        weld = list(range(self.n_input))
        for i, p in enumerate(pos):
            k = (round(p[0] / q), round(p[1] / q), round(p[2] / q))
            weld[i] = bucket.setdefault(k, i)
        for _ in range(4):
            weld = [weld[weld[i]] for i in range(self.n_input)]
        groups = defaultdict(list)
        for i in range(self.n_input):
            groups[weld[i]].append(i)

        # -- geometric key -> canonical id --------------------------------
        def gkey(r):
            p = pos[r]
            return (round(p[0], 9), round(p[1], 9), round(p[2], 9))
        reps = sorted(groups, key=gkey)
        rid = {r: i for i, r in enumerate(reps)}
        self.gid_of_input = [rid[weld[i]] for i in range(self.n_input)]
        self.nv = len(reps)
        self.wpos = [pos[r] for r in reps]
        self.members = [sorted(groups[r]) for r in reps]   # input ids per vertex

        # -- classify triangles -------------------------------------------
        faces, fsrc, degenerate = [], [], []
        for ti, t in enumerate(tris):
            if len(t) != 3:
                degenerate.append(ti)
                continue
            a, b, c = (self.gid_of_input[v] for v in t)
            if a == b or b == c or c == a:
                degenerate.append(ti)
                continue
            p, qq, r = self.wpos[a], self.wpos[b], self.wpos[c]
            if (tri_area3(p, qq, r) <= 1e-14 or norm(sub(qq, p)) < 1e-12 or
                    norm(sub(r, qq)) < 1e-12 or norm(sub(p, r)) < 1e-12):
                degenerate.append(ti)
                continue
            faces.append((a, b, c))
            fsrc.append(ti)
        self.degenerate = degenerate

        self._orient(faces)
        # canonical cyclic rotation (smallest id first) and order
        rot = [self._rotate(f) for f in self.faces]
        order = sorted(range(len(rot)), key=lambda i: rot[i])
        self.faces = [rot[i] for i in order]
        self.fsrc = [fsrc[i] for i in order]
        self.nf = len(self.faces)
        self._edges()

    @staticmethod
    def _rotate(f):
        a, b, c = f
        m = min(a, b, c)
        if a == m:
            return (a, b, c)
        if b == m:
            return (b, c, a)
        return (c, a, b)

    def _face_dirs(self, f, flipped):
        a, b, c = self.faces[f]
        if flipped:
            a, b, c = a, c, b
        return {(u, v) if u < v else (v, u): (1 if u < v else -1)
                for u, v in ((a, b), (b, c), (c, a))}

    def _orient(self, faces):
        adj = defaultdict(list)
        for f, (a, b, c) in enumerate(faces):
            for u, v in ((a, b), (b, c), (c, a)):
                adj[ekey(u, v)].append(f)
        flip = [None] * len(faces)

        def dirs(f, fl):
            a, b, c = faces[f]
            if fl:
                a, b, c = a, c, b
            return {ekey(u, v): (1 if u < v else -1)
                    for u, v in ((a, b), (b, c), (c, a))}
        for seed in range(len(faces)):
            if flip[seed] is not None:
                continue
            flip[seed] = False
            stack = [seed]
            while stack:
                f = stack.pop()
                for e, d in dirs(f, flip[f]).items():
                    if len(adj[e]) != 2:
                        continue
                    g = adj[e][0] if adj[e][1] == f else adj[e][1]
                    if flip[g] is not None:
                        continue
                    flip[g] = (dirs(g, False)[e] != -d)
                    stack.append(g)
        for f, (a, b, c) in enumerate(faces):
            if flip[f]:
                faces[f] = (a, c, b)
        self.faces = faces

    def _edges(self):
        self.adj = defaultdict(list)
        for f, (a, b, c) in enumerate(self.faces):
            for u, v in ((a, b), (b, c), (c, a)):
                self.adj[ekey(u, v)].append(f)

    def elen(self, e):
        return norm(sub(self.wpos[e[0]], self.wpos[e[1]]))

    def face_components(self):
        par = list(range(self.nf))

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        for e in sorted(self.adj):
            fs = self.adj[e]
            if len(fs) == 2:
                a, b = find(fs[0]), find(fs[1])
                if a != b:
                    if a < b:
                        par[b] = a
                    else:
                        par[a] = b
        comps = defaultdict(list)
        for f in range(self.nf):
            comps[find(f)].append(f)
        return [comps[k] for k in sorted(comps)]


# ==========================================================================
# 2. cut to disks
# ==========================================================================
class Chart:
    """Topology of one face set after a seam set has been applied.

    Model.  A *corner* is a (face, vertex) incidence.  Corners at the same
    vertex are glued when the faces meet along the same non-seam manifold edge;
    a seam edge is not glued, so both of its sides keep independent chart
    vertices and may carry independent UVs - the operational meaning of a seam.

    Evaluation.  Chart vertices are corner glue-classes.  A chart *edge* is a
    half-edge pair glued through its non-seam neighbour: the two sides of a
    non-seam edge are one chart edge, whereas the two sides of a seam edge stay
    two distinct chart edges (each used once, hence both on the boundary).
    That distinction is exactly what makes a cut topologically visible.  Then

        chi = V - E + F,   genus = (2 - chi - b) / 2,   disk <=> chi=1, b=1, g=0.
    """

    def __init__(self, mesh, faces, seams):
        self.m = mesh
        self.faces = list(faces)
        self.seams = set(seams)
        self._fset = set(self.faces)

        slot_of = {}
        corners = []
        for f in self.faces:
            for v in mesh.faces[f]:
                slot_of[(f, v)] = len(corners)
                corners.append((f, v))
        self.corners = corners
        self.slot_of = slot_of

        # -- glue corner classes across non-seam manifold edges -----------
        uf = UF(len(corners))
        for f in self.faces:
            tri = mesh.faces[f]
            for s in range(3):
                u, v = tri[s], tri[(s + 1) % 3]
                e = ekey(u, v)
                if e in self.seams:
                    continue
                fs = mesh.adj[e]
                if len(fs) != 2:
                    continue
                g = fs[0] if fs[1] == f else fs[1]
                if g not in self._fset:
                    continue
                uf.union(slot_of[(f, u)], slot_of[(g, u)])
                uf.union(slot_of[(f, v)], slot_of[(g, v)])

        root_id = {}
        self.cvert = []
        for c in corners:
            r = uf.find(slot_of[c])
            if r not in root_id:
                root_id[r] = len(root_id)
            self.cvert.append(root_id[r])
        self.nV = len(root_id)

        # -- chart edges: glue the two sides across non-seam edges only ---
        # raw edge ids are keyed by the *corner pair*, so the two sides of a
        # seam edge never accidentally coincide even when their endpoints do
        raw_of_side = {}                   # (face, edge key) -> raw id
        raw_list = []                      # [(raw id, cu, cv, corner)]
        euf = UF(0)
        raw_count = 0
        for f in self.faces:
            tri = mesh.faces[f]
            for s in range(3):
                u, v = tri[s], tri[(s + 1) % 3]
                e = ekey(u, v)
                cu = self.cvert[slot_of[(f, u)]]
                cv = self.cvert[slot_of[(f, v)]]
                raw_of_side[(f, e)] = raw_count
                raw_list.append((raw_count, cu, cv, slot_of[(f, u)]))
                raw_count += 1
        euf = UF(raw_count)
        for f in self.faces:
            tri = mesh.faces[f]
            for s in range(3):
                u, v = tri[s], tri[(s + 1) % 3]
                e = ekey(u, v)
                if e in self.seams:
                    continue                # seam: keep the two sides apart
                fs = mesh.adj[e]
                if len(fs) != 2:
                    continue                # open boundary / non-manifold
                g = fs[0] if fs[1] == f else fs[1]
                if g not in self._fset:
                    continue
                euf.union(raw_of_side[(f, e)], raw_of_side[(g, e)])

        edge_root = {}
        self.half = []                     # (cu, cv, chart edge id, corner)
        mult = defaultdict(int)
        for raw, cu, cv, corner in raw_list:
            r = euf.find(raw)
            if r not in edge_root:
                edge_root[r] = len(edge_root)
            eid = edge_root[r]
            self.half.append((cu, cv, eid, corner))
            mult[eid] += 1
        self.nE = len(edge_root)
        self.nF = len(self.faces)
        self.chi = self.nV - self.nE + self.nF

        # -- boundary: chart edges used by exactly one side ---------------
        self.bnd_ids = frozenset(e for e, n in mult.items() if n == 1)
        nxt = {}
        for cu, cv, eid, corner in self.half:
            if eid in self.bnd_ids:
                nxt[cu] = cv
        self._bnext = nxt
        self.nloops = _count_loops(nxt)
        # chi = 2 - 2g - b  =>  g = (2 - chi - b) / 2  (b = number of loops)
        self.genus = (2 - self.chi - self.nloops) // 2
        self.ncomponents = len(self.chart_components())

    def _face_set(self):
        return self._fset

    def chart_components(self):
        """Connected components of the chart, as sets of face ids."""
        par = {f: f for f in self.faces}

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        fset = self._face_set()
        for e in sorted(self.m.adj):
            fs = self.m.adj[e]
            if len(fs) != 2 or e in self.seams:
                continue
            f, g = fs
            if f not in fset or g not in fset:
                continue
            a, b = find(f), find(g)
            if a != b:
                if a < b:
                    par[b] = a
                else:
                    par[a] = b
        groups = defaultdict(list)
        for f in self.faces:
            groups[find(f)].append(f)
        return [sorted(groups[k]) for k in sorted(groups)]

    def boundary_loops(self):
        nxt = self._bnext
        loops, seen = [], set()
        for start in sorted(nxt):
            if start in seen:
                continue
            loop, x = [], start
            while x in nxt and x not in seen:
                seen.add(x)
                loop.append(x)
                x = nxt[x]
            if loop:
                loops.append(loop)
        return loops

    def chart_mesh_vertex(self, cv):
        """Canonical mesh vertex of a chart vertex (smallest identical)."""
        if not hasattr(self, "_cv2mv"):
            d = {}
            for i, (f, v) in enumerate(self.corners):
                c = self.cvert[i]
                if c not in d or v < d[c]:
                    d[c] = v
            self._cv2mv = d
        return self._cv2mv[cv]

    def is_disk(self):
        return (self.ncomponents == 1 and self.chi == 1 and
                self.nloops == 1 and self.genus == 0)

    def corner_uv_edges(self):
        """Map chart vertex -> list of (neighbour chart vertex, mesh edge)."""
        if getattr(self, "_cuve", None) is not None:
            return self._cuve
        adj = defaultdict(list)
        seen = set()
        for cu, cv, eid, corner in self.half:
            if (cu, cv, eid) in seen or (cv, cu, eid) in seen:
                continue
            seen.add((cu, cv, eid))
            f, v = self.corners[corner]
            tri = self.m.faces[f]
            s = tri.index(v)
            me = ekey(v, tri[(s + 1) % 3])
            adj[cu].append((cv, me))
            adj[cv].append((cu, me))
        for k in adj:
            adj[k].sort()
        self._cuve = adj
        return adj

    def join_path(self, cv_a, cv_b):
        """Shortest chart-level path between two chart vertices.

        Returns the list of mesh edges to cut, guaranteed non-empty when the
        two chart vertices are different even if they map to the same mesh
        vertex (which is exactly the situation after earlier cuts, and the
        reason a mesh-vertex shortest path degenerates to length zero).
        """
        if cv_a == cv_b:
            return []
        # the chart adjacency is cached: join_path is called once per loop-vertex
        # pair, so rebuilding it would make loop-joining quadratic
        adj = self.corner_uv_edges()
        dist = {cv_a: 0.0}
        prev = {}
        pq = [(0.0, cv_a)]
        done = set()
        while pq:
            d, x = heapq.heappop(pq)
            if x in done:
                continue
            done.add(x)
            if x == cv_b:
                break
            for y, me in adj.get(x, []):
                nd = d + self.m.elen(me)
                if y not in dist or nd < dist[y] - 1e-15:
                    dist[y] = nd
                    prev[y] = (x, me)
                    heapq.heappush(pq, (nd, y))
        if cv_b not in prev:
            return []
        path, x = [], cv_b
        while x != cv_a:
            px, me = prev[x]
            path.append(me)
            x = px
        return path[::-1]


def _count_loops(nxt):
    seen = set()
    loops = 0
    for start in sorted(nxt):
        if start in seen:
            continue
        loops += 1
        x = start
        while x in nxt and x not in seen:
            seen.add(x)
            x = nxt[x]
    return loops


class Cutter:
    """Irredundant seam set turning every connected component into a disk.

    Every phase evaluates its candidate move on a *trial* chart and accepts it
    only when the whole chart strictly improves, so the routine is robust on
    coarse meshes, multiple components and degenerate input, and it can never
    loop forever:

    * opening    - a closed chart (chi = 2 - 2g, no boundary) is opened first;
                   candidate openings are scored by real flattening quality, so
                   a degenerate stub is rejected in favour of a short path.
    * loop join  - while several boundary loops remain, two loops are joined by
                   a shortest path, searched at chart level (robust when loops
                   share mesh vertices) with a mesh-level fallback.
    * genus      - while a handle remains, add a cycle that strictly lowers the
                   genus; boundary loops may temporarily increase and are
                   re-joined on the next pass.
    * prune      - remove any seam edge that still leaves every chart a disk.
    * polish     - iteratively add short cycles that reduce folded triangles,
                   then prune again, so the seam count never grows.
    """

    def __init__(self, mesh):
        self.m = mesh

    def shortest_path(self, src, dst, allowed):
        adjv = defaultdict(list)
        for e in allowed:
            adjv[e[0]].append((e[1], e))
            adjv[e[1]].append((e[0], e))
        for v in adjv:
            adjv[v].sort()
        dist = {src: 0.0}
        prev = {}
        pq = [(0.0, src)]
        done = set()
        while pq:
            d, x = heapq.heappop(pq)
            if x in done:
                continue
            done.add(x)
            if x == dst:
                break
            for y, e in adjv[x]:
                nd = d + self.m.elen(e)
                if y not in dist or nd < dist[y] - 1e-15:
                    dist[y] = nd
                    prev[y] = (x, e)
                    heapq.heappush(pq, (nd, y))
        if dst != src and dst not in prev:
            return []
        path, x = [], dst
        while x != src:
            px, e = prev[x]
            path.append(e)
            x = px
        return path[::-1]

    def comp_edges(self, comp):
        es = set()
        for f in comp:
            a, b, c = self.m.faces[f]
            es.add(ekey(a, b))
            es.add(ekey(b, c))
            es.add(ekey(c, a))
        return es

    def _candidate_cycles(self, comp, allowed):
        """Shortest cycles worth trying as a genus cut, deterministically."""
        parent = {}

        def find(x):
            parent.setdefault(x, x)
            while parent[x] != x:
                parent[x] = parent[parent[x]]
                x = parent[x]
            return x
        tree, chords = set(), []
        for e in sorted(allowed, key=lambda e: (self.m.elen(e), e)):
            a, b = find(e[0]), find(e[1])
            if a != b:
                parent[b] = a
                tree.add(e)
            else:
                chords.append(e)
        cycles = []
        for e in sorted(set(chords), key=lambda e: (self.m.elen(e), e))[:32]:
            p = self.shortest_path(e[0], e[1], tree)
            if p:
                cycles.append(p + [e])
        for e in sorted(set(chords), key=lambda e: (self.m.elen(e), e))[:32]:
            p = self.shortest_path(e[0], e[1], allowed - {e})
            if p:
                cycles.append(p + [e])
        for e in sorted(allowed, key=lambda e: (self.m.elen(e), e))[:24]:
            p = self.shortest_path(e[0], e[1], allowed - {e})
            if p:
                cycles.append(p + [e])
        uniq = {}
        for cyc in cycles:
            k = tuple(sorted(set(cyc)))
            L = sum(self.m.elen(x) for x in cyc)
            if k not in uniq or L < uniq[k][0]:
                uniq[k] = (L, cyc)
        out = sorted(uniq.values(), key=lambda t: (t[0], tuple(sorted(set(t[1])))))
        return [c for _, c in out]

    def cut(self):
        seams = set()
        for comp in self.m.face_components():
            seams |= self._cut_component(sorted(comp), set())
        # verify: every chart is a disk; if any is not, split it further
        seams |= self.repair(seams)
        # Improve the flattening first (cheap topological passes), then do the
        # final irredundancy pass *last* so its flip guard sees the finished
        # cut and can skip the expensive LSCM checks when there are no folds.
        seams = self._polish(seams)
        seams = self._prune(seams)
        return sorted(seams)

    def _polish(self, seams):
        """Iterated local search lowering folded-triangle count.

        On nested topologies (genus 2+) the greedy phase order can leave a
        generator in a poor position even when the *count* is minimal.  Each
        sweep tries every short candidate cycle as an extra cut and keeps any
        addition that strictly reduces folded triangles; a pruning step then
        removes whatever became redundant, so the seam count stays minimal.
        Sweeps repeat until no strict improvement is found.
        """
        best = set(seams)
        best_flips = self._flip_count(best)
        for _ in range(4):
            if best_flips == 0:
                break
            improved = False
            for comp in self.m.face_components():
                comp = sorted(comp)
                allowed = self.comp_edges(comp)
                for cyc in self._candidate_cycles(comp, allowed)[:24]:
                    trial = best | set(cyc)
                    isls = self.islands(trial)
                    if not all(Chart(self.m, isl, trial).is_disk()
                               for isl in isls):
                        continue
                    fl = self._flip_count(trial)
                    if fl < best_flips:
                        best = trial
                        best_flips = fl
                        improved = True
                        break
                if improved:
                    break
            if not improved:
                break
        return self._prune(best)

    def _prune(self, seams):
        """Drop seam edges that are not needed, keeping a usable unwrap.

        Topology alone cannot rank two valid cuts: on a closed surface both a
        short stub and a meridian path give a topological disk, yet the stub
        flattens into a degenerate sliver.  So an edge is removed only when

        1. every chart remains a single disk,
        2. the remaining seam still splits a vertex fan, and
        3. the flattening does not get worse (folded-triangle count).

        The flattening test is skipped entirely when the original surface is
        already open: an open chart has a real polygonal boundary, so no cut can
        collapse it, and skipping the LSCM solve keeps pruning cheap.
        """
        seams = set(seams)
        if not seams:
            return seams
        has_boundary = any(len(fs) != 2 for fs in self.m.adj.values())
        comps = [sorted(c) for c in self.m.face_components()]

        def all_disks(sset):
            for comp in comps:
                ch = Chart(self.m, comp, sset)
                if not ch.is_disk():
                    return False
            return True

        current = 0 if has_boundary else self._flip_count(seams)
        check_flips = (not has_boundary) and current > 0

        # Fast path: a seam graph with no *dangling* edge and whose cycle rank
        # already equals the topological minimum cannot lose an edge and stay a
        # valid cut, so the scan is skipped.  This is the common case (a clean
        # torus has exactly 2g cycles) and it avoids |seams| full chart builds.
        if self._seam_graph_minimal(seams, comps) and not check_flips:
            return seams

        changed = True
        while changed:
            changed = False
            for e in sorted(seams):
                trial = seams - {e}
                if not all_disks(trial):
                    continue
                if not self._splits_a_fan(trial, self.islands(trial)):
                    continue
                if check_flips:
                    fl = self._flip_count(trial)
                    if fl > current:
                        continue
                    current = fl
                seams = trial
                changed = True
                break
        return seams

    def _seam_graph_minimal(self, seams, comps):
        """True when no single seam edge can be dropped and stay a valid cut.

        A seam edge is droppable only if the cut graph still separates the
        surface afterwards.  For a cut built from g generators plus handle
        links, every edge lies on an essential cycle, so a cheap sufficient
        test is: the seam graph has no vertex of degree 1 and its cycle rank
        equals the total genus excess of the components (2g for closed ones).
        When that holds, `_prune` cannot remove anything and is skipped.
        """
        inc = defaultdict(int)
        for a, b in seams:
            inc[a] += 1
            inc[b] += 1
        if any(d == 1 for d in inc.values()):
            return False
        # cycle rank of the seam graph
        par = {}

        def find(x):
            par.setdefault(x, x)
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        comps_count = 0
        verts = set()
        for a, b in seams:
            verts.add(a)
            verts.add(b)
            ra, rb = find(a), find(b)
            if ra != rb:
                par[rb] = ra
        comps_count = len({find(v) for v in verts})
        rank = len(seams) - len(verts) + comps_count

        # required rank: 2g per closed component (2g + (b-1) if bordered)
        required = 0
        for comp in comps:
            ch = Chart(self.m, comp, set())
            g = max(0, (2 - ch.chi - ch.nloops) // 2)
            required += 2 * g + max(0, ch.nloops - 1)
        return rank == required

    def _flip_count(self, seams):
        """Number of folded triangles after flattening every island."""
        total = 0
        for isl in self.islands(seams):
            ch = Chart(self.m, isl, seams)
            if not ch.is_disk():
                return 1 << 30
            uv, cvs = LSCM(self.m, isl, ch).solve()
            lut = {cv: uv[i] for i, cv in enumerate(cvs)}
            for f in isl:
                tri = [lut[ch.cvert[ch.slot_of[(f, v)]]] for v in self.m.faces[f]]
                if tri_uv_area(tuple(tri)) <= 0.0:
                    total += 1
        return total

    def _splits_a_fan(self, seams, islands):
        """True if some mesh vertex appears as >= 2 chart vertices."""
        for isl in islands:
            ch = Chart(self.m, isl, seams)
            seen = defaultdict(set)
            for i, (f, v) in enumerate(ch.corners):
                seen[v].add(ch.cvert[i])
            if any(len(s) > 1 for s in seen.values()):
                return True
        return False

    def _cut_component(self, comp, seams):
        added = set()
        for _ in range(8 * (len(comp) + 4)):
            ch = Chart(self.m, comp, seams | added)
            if ch.is_disk():
                return added
            allowed = self.comp_edges(comp)
            progressed = False

            # --- stage A: a closed component must be opened at all --------
            # With no boundary the chart is a closed surface (chi = 2 - 2g).
            # Open it with one edge first; the remaining phases then see a
            # bordered chart, which is the case they reason about.
            if ch.nloops == 0:
                opened = self._open_closed(comp, seams | added, ch, allowed)
                if opened:
                    added |= opened
                    continue

            # --- stage B: join extra boundary loops -----------------------
            # Joining happens at the *chart* level: after earlier cuts two
            # boundary loops can share all their mesh vertices, so a mesh-space
            # shortest path would degenerate to zero length.  A chart-level BFS
            # through chart edges always yields a real, non-empty cut.
            if ch.nloops > 1:
                loops = ch.boundary_loops()
                cand = []
                # (a) chart-level joins: robust when loops share mesh vertices.
                # Only the first few vertices of each loop are paired, so this
                # stays linear rather than quadratic in the loop length.
                reps = [lp[0] for lp in loops]
                for i in range(len(loops)):
                    for j in range(i + 1, len(loops)):
                        ai = [reps[i]] + loops[i][:6]
                        bj = [reps[j]] + loops[j][:6]
                        for ca in ai:
                            for cb in bj:
                                path = ch.join_path(ca, cb)
                                if not path:
                                    continue
                                L = sum(self.m.elen(e) for e in path)
                                cand.append((L, tuple(sorted(set(path))), path))
                # (b) mesh-level joins as well: usually shortest
                for i in range(len(loops)):
                    ai = sorted({ch.chart_mesh_vertex(v) for v in loops[i]})
                    for j in range(i + 1, len(loops)):
                        bj = sorted({ch.chart_mesh_vertex(v) for v in loops[j]})
                        best = None
                        for a in ai:
                            for b in bj:
                                d = self.elen_pair(a, b)
                                if best is None or (d, a, b) < best[0]:
                                    best = ((d, a, b), a, b)
                        if best is not None:
                            _, a, b = best
                            path = self.shortest_path(a, b, allowed)
                            if path:
                                L = sum(self.m.elen(e) for e in path)
                                cand.append((L, tuple(sorted(set(path))), path))
                cand.sort(key=lambda t: (t[0], t[1]))
                seen_paths = set()
                for _, key, path in cand:
                    if key in seen_paths:
                        continue
                    seen_paths.add(key)
                    trial = added | set(path)
                    ch2 = Chart(self.m, comp, seams | trial)
                    if (ch2.ncomponents == 1 and
                            ch2.nloops < ch.nloops and
                            ch2.genus <= ch.genus):
                        added = trial
                        progressed = True
                        break
                    if len(seen_paths) >= 24:
                        break
                if progressed:
                    continue

            # --- stage C: remove handles ----------------------------------
            # Cut a cycle that strictly lowers genus.  The loop count may rise
            # temporarily (cutting a handle can expose an extra boundary), and
            # stage B merges loops again on the next pass; what must never
            # happen is standing still, so any strict genus drop is accepted.
            if ch.genus > 0:
                for cyc in self._candidate_cycles(comp, allowed):
                    trial = added | set(cyc)
                    ch2 = Chart(self.m, comp, seams | trial)
                    if (ch2.ncomponents == 1 and ch2.genus < ch.genus and
                            ch2.chi > ch.chi):
                        added = trial
                        progressed = True
                        break
                if progressed:
                    continue

            # --- stage D: fall back to any single edge / cycle that helps --
            # Independent single edges are also legitimate cuts (they may be
            # needed when no short cycle exists yet), so they are tried here.
            best = None
            for cyc in self._candidate_cycles(comp, allowed):
                trial = added | set(cyc)
                ch2 = Chart(self.m, comp, seams | trial)
                if ch2.ncomponents != 1:
                    continue
                key = (ch2.genus, ch2.nloops, -ch2.chi)
                if key < (ch.genus, ch.nloops, -ch.chi):
                    if best is None or key < best[0]:
                        best = (key, trial)
            if best is not None:
                added = best[1]
                progressed = True
                continue
            if not progressed:
                break
        return added

    def _open_closed(self, comp, seams, ch, allowed):
        """Open a closed component so the later phases can reach a good disk.

        Topologically a closed chart (chi = 2 - 2g) only needs a boundary, but
        the *quality* of the resulting unwrap depends on what is cut:

        * a single edge gives a degenerate two-gon boundary and splits no vertex
          fan, so on a closed genus-0 surface (a sphere) the flattening
          collapses to zero area;
        * a path that splits fans gives a proper polygonal boundary and flattens
          well, but on a torus a long path wastes seam length compared with the
          single-edge opening plus short generators.

        Both candidates are therefore tried and scored by the actual unwrap, and
        the better one (fewest folded triangles, then fewest seam edges) wins.
        This is a measurement, not a guess, so it behaves correctly on spheres,
        tori and higher genus alike.
        """
        used = set()
        for f in comp:
            used.update(self.m.faces[f])
        vs = sorted(used)
        if len(vs) < 2:
            return set()

        candidates = []
        shortest = sorted(allowed, key=lambda e: (self.m.elen(e), e))
        if shortest:
            candidates.append({shortest[0]})

        pairs = []
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                pairs.append((-self.elen_pair(vs[i], vs[j]), vs[i], vs[j]))
        pairs.sort()
        for _, a, b in pairs[:4]:
            path = self.shortest_path(a, b, allowed)
            if path:
                candidates.append(set(path))

        best = None
        for cand in candidates:
            trial = seams | cand
            ch2 = Chart(self.m, comp, trial)
            if ch2.ncomponents != 1 or ch2.nloops < 1:
                continue
            score = (self._flip_count_comp(comp, trial), len(cand))
            if best is None or score < best[0]:
                best = (score, cand)
        if best is not None:
            return set(best[1])

        for e in shortest:
            ch3 = Chart(self.m, comp, seams | {e})
            if ch3.ncomponents == 1 and ch3.nloops >= 1:
                return {e}
        return set()

    def _flip_count_comp(self, comp, seams):
        """Folded triangles over one component's chart after flattening."""
        ch = Chart(self.m, comp, seams)
        if not ch.is_disk():
            # not yet a disk: rank by topology first (fewer loops/genus better)
            return 1 << 20
        uv, cvs = LSCM(self.m, comp, ch).solve()
        lut = {cv: uv[i] for i, cv in enumerate(cvs)}
        bad = 0
        for f in comp:
            tri = [lut[ch.cvert[ch.slot_of[(f, v)]]] for v in self.m.faces[f]]
            if tri_uv_area(tuple(tri)) <= 0.0:
                bad += 1
        return bad

    def repair(self, seams):
        """Final safety net: every chart must be a disk.

        Runs the per-component cutter again on any chart that is still not a
        disk; each pass strictly increases the seam set, so it terminates.
        """
        extra = set()
        for _ in range(8):
            bad = [isl for isl in self.islands(seams | extra)
                   if not Chart(self.m, isl, seams | extra).is_disk()]
            if not bad:
                break
            for isl in bad:
                sub = self._cut_component(sorted(isl), seams | extra)
                if not sub:
                    break
                extra |= sub
        return extra

    def elen_pair(self, a, b):
        return norm(sub(self.m.wpos[a], self.m.wpos[b]))

    def islands(self, seams):
        """Connected face charts under a seam set."""
        par = list(range(self.m.nf))

        def find(x):
            while par[x] != x:
                par[x] = par[par[x]]
                x = par[x]
            return x
        for e in sorted(self.m.adj):
            fs = self.m.adj[e]
            if len(fs) == 2 and e not in seams:
                a, b = find(fs[0]), find(fs[1])
                if a != b:
                    if a < b:
                        par[b] = a
                    else:
                        par[a] = b
        groups = defaultdict(list)
        for f in range(self.m.nf):
            groups[find(f)].append(f)
        return [sorted(groups[k]) for k in sorted(groups)]


# ==========================================================================
# 3. LSCM conformal parameterisation
# ==========================================================================
class LSCM:
    """Least squares conformal map (Levy et al. 2002) of a triangulated disk.

    Per triangle with CCW vertices p0,p1,p2 the discrete conformal energy is

        E_T = 1/2 sum_ij cot(theta_ij) (|z_i - z_j|^2)
              - 1/2 [ (u1-u0)(v2-v0) - (u2-u0)(v1-v0) ]

    with theta_ij the angle opposite edge (i,j).  This is exactly the discretised
    sum of squares  (1/2) int_T (u_x - v_y)^2 + (u_y + v_x)^2 dA, so the Hessian
    is symmetric positive semi-definite with a 4-dimensional kernel of
    similarity transforms.  Two pinned vertices (a canonical farthest pair)
    remove the kernel; the reduced system is solved by Gauss-Jordan.

    Variable layout: index 2*i is u_i, 2*i+1 is v_i for chart-local vertex i.
    """

    def __init__(self, mesh, faces, chart):
        self.m = mesh
        self.faces = list(faces)
        self.chart = chart

    def solve(self):
        """Return (uv per chart vertex, ordered chart vertex ids)."""
        m, ch = self.m, self.chart
        # canonical local order: sort chart vertices by their smallest mesh id
        first = {}
        for i, (f, v) in enumerate(ch.corners):
            cv = ch.cvert[i]
            if cv not in first or v < first[cv]:
                first[cv] = v
        cvs = sorted(first, key=lambda cv: first[cv])
        loc = {cv: i for i, cv in enumerate(cvs)}
        n = len(cvs)
        if n < 3:
            return [(0.0, 0.0)] * n, cvs
        tris = [[loc[ch.cvert[ch.slot_of[(f, v)]]] for v in m.faces[f]]
                for f in self.faces]
        pos = [m.wpos[first[cv]] for cv in cvs]
        return self._solve(n, tris, pos), cvs

    def _solve(self, n, tris, pos):
        # Sparse assembly: LSCM couples a vertex only to its neighbours, so the
        # Hessian is kept as per-row adjacency lists.  After pinning it is SPD
        # on the free variables, hence preconditioned conjugate gradients.
        rows = [dict() for _ in range(2 * n)]      # rows[k][j] = H[k][j]

        def addH(i, j, val):
            if val == 0.0:
                return
            rows[i][j] = rows[i].get(j, 0.0) + val

        for tri in tris:
            a, b, c = tri
            p, q, r = pos[a], pos[b], pos[c]
            ta, tb, tc = tri_angles3(p, q, r)
            wedges = (((a, b), 0.5 * cot(tc)),
                      ((b, c), 0.5 * cot(ta)),
                      ((c, a), 0.5 * cot(tb)))
            for (j, k), w in wedges:
                d = 2.0 * w
                for off in (0, 1):
                    hj, hk = 2 * j + off, 2 * k + off
                    addH(hj, hj, d)
                    addH(hk, hk, d)
                    addH(hj, hk, -d)
                    addH(hk, hj, -d)
            # Energy is  E = W - 2 A_uv, and
            #   A_uv = 1/2 [ (u1-u0)(v2-v0) - (u2-u0)(v1-v0) ]
            # Expanding and collecting the bilinear form  sum_{ik} C[i][k] u_i v_k:
            #   2 A_uv = u1v2 - u1v0 - u0v2 - u2v1 + u2v0 + u0v1
            # hence  -2 A_uv  contributes C[u_i][v_k] = -coef, where coef is the
            # six terms below and each (i,k) appears exactly once.  The Hessian
            # must be symmetric, so the transposed entry is set to the same
            # value; crucially the (i,k) and (k,i) terms are *different*
            # coefficients and must not be merged.
            L = (a, b, c)
            C = (((1, 2), -1.0), ((1, 0), 1.0), ((0, 2), 1.0),
                 ((2, 1), 1.0), ((2, 0), -1.0), ((0, 1), -1.0))
            for (iu, kv), g in C:
                ui = 2 * L[iu]
                vk = 2 * L[kv] + 1
                addH(ui, vk, g)
                addH(vk, ui, g)

        # pin a canonical farthest pair
        p0, p1 = farthest_pair(pos)
        dist = norm(sub(pos[p1], pos[p0]))
        if dist < 1e-12:
            dist = 1.0
        pinidx = {2 * p0: 0.0, 2 * p0 + 1: 0.0, 2 * p1: dist, 2 * p1 + 1: 0.0}

        pin_terms = {}
        for i in range(2 * n):
            if i in pinidx:
                continue
            s = 0.0
            for k, val in pinidx.items():
                s += rows[i].get(k, 0.0) * val
            if s != 0.0:
                pin_terms[i] = -s

        sol = cg_solve(rows, pinidx, pin_terms, 2 * n)
        # Very small charts are cheap to factorise directly, which removes any
        # doubt about CG convergence on extremely ill-conditioned coarse
        # patches.  The cap keeps this well inside interactive time.
        if 2 * n <= 300:
            direct = self._direct_solve(rows, pinidx, pin_terms, 2 * n)
            if direct is not None:
                sol = direct
        x = [0.0] * (2 * n)
        for k, val in pinidx.items():
            x[k] = val
        for i in range(2 * n):
            if i not in pinidx:
                x[i] = sol[i]
        return [(x[2 * i], x[2 * i + 1]) for i in range(n)]

    @staticmethod
    def _direct_solve(rows, pinidx, rhs, n):
        """Sparse-row direct solve of the pinned system, or None if degenerate."""
        free = [k for k in range(n) if k not in pinidx]
        A = [[rows[i].get(j, 0.0) for j in free] for i in free]
        b = [rhs.get(i, 0.0) for i in free]
        sol = solve_dense(A, b)
        x = [0.0] * n
        for k, val in enumerate(sol):
            x[free[k]] = val
        return x


def cot(theta):
    s = math.sin(theta)
    if abs(s) < 1e-14:
        return 0.0
    res = math.cos(theta) / s
    # guard against near-degenerate triangles blowing up the system
    return max(-1e8, min(1e8, res))


def tri_angles3(p, q, r):
    return (angle_at(p, q, r), angle_at(q, r, p), angle_at(r, p, q))


def farthest_pair(pos):
    """Canonical diameter pair (ties broken by index).

    Only the pinned gauge depends on this choice; for pinning we just need two
    vertices that are far apart and chosen in a reproducible way.  The extremes
    along the three coordinate axes are always a good spread and cost O(n)
    instead of the O(n^2) all-pairs scan.
    """
    n = len(pos)
    if n < 2:
        return 0, 0
    cand = set()
    for axis in range(3):
        lo = min(range(n), key=lambda i: (pos[i][axis], i))
        hi = max(range(n), key=lambda i: (pos[i][axis], -i))
        cand.add(lo)
        cand.add(hi)
    cand = sorted(cand)
    best = None
    for ii in range(len(cand)):
        for jj in range(ii + 1, len(cand)):
            a, b = cand[ii], cand[jj]
            d2 = ((pos[a][0] - pos[b][0]) ** 2 +
                  (pos[a][1] - pos[b][1]) ** 2 +
                  (pos[a][2] - pos[b][2]) ** 2)
            key = (-d2, a, b)
            if best is None or key < best[0]:
                best = (key, a, b)
    if best is None:
        return 0, 0
    return best[1], best[2]


def cg_solve(rows, pinidx, rhs, n, tol=1e-12, maxit=None):
    """Jacobi-preconditioned conjugate gradients on the free variables.

    rows[k] is a sparse row dict of the full Hessian; the pinned variables are
    eliminated (their contributions already sit in `rhs`) and the iteration runs
    purely over the free set, stored densely in local indices so the inner loop
    has no membership tests.  Returns the full-length solution with zeros at the
    pinned positions (the caller writes the pinned values back).
    """
    x = [0.0] * n
    free = [k for k in range(n) if k not in pinidx]
    nf = len(free)
    if nf == 0:
        return x
    loc = {k: i for i, k in enumerate(free)}
    # compact sparse rows over the free set, and the Jacobi diagonal
    srows = []
    diag = [0.0] * nf
    for i, k in enumerate(free):
        row = []
        for j, v in rows[k].items():
            if j in pinidx:
                continue
            jj = loc.get(j)
            if jj is not None:
                row.append((jj, v))
        srows.append(row)
        d = rows[k].get(k, 0.0)
        diag[i] = d if abs(d) > 1e-14 else 1.0
    invdiag = [1.0 / d for d in diag]

    def matvec(vec):
        out = [0.0] * nf
        for i in range(nf):
            s = 0.0
            for j, v in srows[i]:
                s += v * vec[j]
            out[i] = s
        return out

    # Jacobi preconditioner (diagonal): one multiply per unknown per iteration,
    # which is the cheapest option in pure Python and empirically converges in
    # a few hundred iterations for the mesh sizes this tool targets.
    def precond(r):
        return [r[i] * invdiag[i] for i in range(nf)]

    def dot2(a, b):
        s = 0.0
        for i in range(nf):
            s += a[i] * b[i]
        return s

    b = [rhs.get(k, 0.0) for k in free]
    bnorm = math.sqrt(dot2(b, b))
    if bnorm < 1e-300:
        return x
    if maxit is None:
        maxit = max(64, 4 * nf)

    # start from the diagonally scaled right-hand side
    xv = [b[i] * invdiag[i] for i in range(nf)]
    Ax = matvec(xv)
    r = [b[i] - Ax[i] for i in range(nf)]
    z = precond(r)
    p = list(z)
    rz = dot2(r, z)
    for _ in range(maxit):
        Ap = matvec(p)
        pAp = dot2(p, Ap)
        if abs(pAp) < 1e-300:
            break
        alpha = rz / pAp
        for i in range(nf):
            xv[i] += alpha * p[i]
            r[i] -= alpha * Ap[i]
        if math.sqrt(dot2(r, r)) <= tol * bnorm:
            break
        z = precond(r)
        rz_new = dot2(r, z)
        if abs(rz) < 1e-300:
            break
        beta = rz_new / rz
        rz = rz_new
        for i in range(nf):
            p[i] = z[i] + beta * p[i]
    for i, k in enumerate(free):
        x[k] = xv[i]
    return x


def solve_dense(A, b):
    """Gauss-Jordan elimination with partial pivoting."""
    n = len(b)
    if n == 0:
        return []
    M = [list(A[i]) + [b[i]] for i in range(n)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(M[r][col]))
        if abs(M[piv][col]) < 1e-13:
            continue
        if piv != col:
            M[col], M[piv] = M[piv], M[col]
        row = M[col]
        inv = 1.0 / row[col]
        for k in range(col, n + 1):
            row[k] *= inv
        for r in range(n):
            if r == col:
                continue
            fac = M[r][col]
            if fac == 0.0:
                continue
            rr = M[r]
            for k in range(col, n + 1):
                rr[k] -= fac * row[k]
    return [M[i][n] for i in range(n)]


# ==========================================================================
# 4. distortion metrics
# ==========================================================================
def tri_uv_area(uvs):
    (x0, y0), (x1, y1), (x2, y2) = uvs
    return 0.5 * ((x1 - x0) * (y2 - y0) - (y1 - y0) * (x2 - x0))


def singular_values(pos, uvs):
    """Singular values (s1 >= s2) of the 3-D -> UV triangle affine map."""
    p0, p1, p2 = pos
    e1, e2 = sub(p1, p0), sub(p2, p0)
    q0, q1, q2 = uvs
    f1 = (q1[0] - q0[0], q1[1] - q0[1], 0.0)
    f2 = (q2[0] - q0[0], q2[1] - q0[1], 0.0)
    l1 = norm(e1)
    n = cross(e1, e2)
    ln = norm(n)
    if l1 < 1e-16 or ln < 1e-18:
        return (1.0, 1.0)
    x = (e1[0] / l1, e1[1] / l1, e1[2] / l1)
    z = (n[0] / ln, n[1] / ln, n[2] / ln)
    y = cross(z, x)
    # source triangle in its own plane, target triangle in UV
    A = [[l1, dot(e2, x)], [0.0, dot(e2, y)]]
    B = [[f1[0], f2[0]], [f1[1], f2[1]]]
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    if abs(det) < 1e-18:
        return (1.0, 1.0)
    Ai = [[A[1][1] / det, -A[0][1] / det], [-A[1][0] / det, A[0][0] / det]]
    J = [[B[i][0] * Ai[0][j] + B[i][1] * Ai[1][j] for j in range(2)]
         for i in range(2)]
    a, b = J[0]
    c, d = J[1]
    tr = a * a + b * b + c * c + d * d
    dt = (a * d - b * c) ** 2
    disc = max(0.0, tr * tr - 4.0 * dt)
    s1sq = 0.5 * (tr + math.sqrt(disc))
    s2sq = 0.5 * (tr - math.sqrt(disc))
    return (math.sqrt(max(s1sq, 0.0)), math.sqrt(max(s2sq, 0.0)))


def island_metrics(mesh, faces, cmap):
    """Distortion statistics of one island.

    cmap: (face, vertex) -> (u, v)

      energy      scale-invariant conformal distortion
                      sum_T A3 (s1 - s2)^2 / (sum_T A3 * s^2)
                  where s^2 = A_uv / A_3 is the global area scale.  It is >= 0
                  and vanishes iff every triangle is mapped conformally, so a
                  uniformly scaled conformal map (e.g. the exact cylinder
                  developable unwrap, or a planar disk) scores exactly 0.
      angle_err   max over corners |angle_uv - angle_3d| (radians).
      area_dist   max_T |log(A_uv / (scale * A_3))| with scale chosen so the
                  mean ratio is one: scale-invariant area distortion, zero when
                  the map is a pure similarity per triangle.
      flipped     triangles with non-positive UV area (should be 0).
    """
    recs = []
    total3 = 0.0
    totaluv = 0.0
    for f in faces:
        a, b, c = mesh.faces[f]
        p, q, r = mesh.wpos[a], mesh.wpos[b], mesh.wpos[c]
        A3 = tri_area3(p, q, r)
        uvs = (cmap[(f, a)], cmap[(f, b)], cmap[(f, c)])
        AU = tri_uv_area(uvs)
        total3 += A3
        totaluv += AU
        recs.append((A3, AU, uvs, (p, q, r)))
    scale = (totaluv / total3) if total3 > 1e-18 and totaluv > 1e-18 else 1.0
    s_lin = math.sqrt(abs(scale)) if scale > 1e-18 else 1.0
    acc = 0.0
    angle_err = 0.0
    area_dist = 0.0
    flipped = 0
    for A3, AU, uvs, pos in recs:
        if AU <= 0.0 or A3 <= 1e-18:
            flipped += 1
            continue
        s1, s2 = singular_values(pos, uvs)
        # conformal residual of the *shape*: divide out the global scale
        acc += A3 * ((s1 - s2) / s_lin) ** 2
        ratio = AU / (A3 * scale)
        area_dist = max(area_dist, abs(math.log(ratio)) if ratio > 1e-18 else 40.0)
        a3 = tri_angles3(*pos)
        a2 = tri_angles3(*[(u[0], u[1], 0.0) for u in uvs])
        for k in range(3):
            angle_err = max(angle_err, abs(a3[k] - a2[k]))
    # Return the *unnormalised* numerator: islands have independent similarity
    # gauges, so normalisation is done once, area-weighted, over all islands.
    energy = acc
    return {"energy": energy, "angle_err": angle_err, "area_dist": area_dist,
            "flipped": flipped, "total_area_3d": total3,
            "total_area_uv": totaluv, "scale": scale}


# ==========================================================================
# 5. seam polylines
# ==========================================================================
def seam_paths(seams):
    """Split a seam edge set into maximal polylines, in canonical order."""
    adjv = defaultdict(set)
    for u, v in seams:
        adjv[u].add(v)
        adjv[v].add(u)
    used = set()
    paths = []
    # start from non-manifold-of-the-seam-graph vertices first, then cycles
    starts = sorted(v for v in adjv if len(adjv[v]) != 2) + sorted(adjv)
    for s in starts:
        for n0 in sorted(adjv[s]):
            e = ekey(s, n0)
            if e in used:
                continue
            used.add(e)
            path = [s, n0]
            prev, cur = s, n0
            while len(adjv[cur]) == 2:
                nxt = [w for w in sorted(adjv[cur]) if w != prev]
                if not nxt:
                    break
                w = nxt[0]
                e2 = ekey(cur, w)
                if e2 in used:
                    break
                used.add(e2)
                path.append(w)
                prev, cur = cur, w
            paths.append(path)
    unique = {}
    for p in paths:
        k = tuple(p) if p[0] <= p[-1] else tuple(reversed(p))
        if k not in unique or len(p) > len(unique[k]):
            unique[k] = p
    out = list(unique.values())
    out.sort(key=lambda p: (min(p), max(p), len(p)))
    return out


# ==========================================================================
# 6. digest
# ==========================================================================
def make_digest(seam_geo, island_geo, uv_geo):
    """Order-independent hash of the unwrap result.

    All arguments are expressed in *geometry* (quantised positions), never in
    vertex indices, so renumbering the input cannot change the digest.
    """
    h = hashlib.sha256()
    h.update(b"seams")
    for a, b in sorted(seam_geo):
        h.update((a + "|" + b + ";").encode())
    h.update(b"islands")
    for sig in sorted(island_geo):
        h.update((sig + ";").encode())
    h.update(b"uv")
    for key, u, v in sorted(uv_geo):
        h.update(("%s:%s,%s;" % (key, _q(u), _q(v))).encode())
    return h.hexdigest()[:16]


def _q(x):
    """Quantise a UV coordinate to a stable decimal string."""
    return "%.6f" % (0.0 if abs(x) < 5e-7 else x)


def _poskey(p):
    """Stable geometric key for a 3-D position."""
    return "%.6f,%.6f,%.6f" % (0.0 if abs(p[0]) < 5e-7 else p[0],
                               0.0 if abs(p[1]) < 5e-7 else p[1],
                               0.0 if abs(p[2]) < 5e-7 else p[2])



# ==========================================================================
# 7. driver
# ==========================================================================
def unwrap(pos, tris):
    """Full pipeline.  Returns the output dictionary (input-indexed)."""
    mesh = Canonical(pos, tris)
    n_in = mesh.n_input

    if mesh.nf == 0:
        return {
            "uv": [[0.0, 0.0] for _ in range(n_in)],
            "uv_faces": [[[0.0, 0.0]] * 3 for _ in range(len(tris))],
            "seams": [], "seam_paths": [], "islands": 0, "island_faces": [],
            "energy": 0.0, "angle_err": 0.0, "area_dist": 0.0,
            "flipped": 0, "severed": [],
            "stats": {"vertices": mesh.nv, "faces": 0,
                      "degenerate_faces": len(mesh.degenerate),
                      "seam_edges": 0, "area_3d": 0.0, "area_uv": 0.0},
            "digest": make_digest([], [], []),
        }

    cutter = Cutter(mesh)
    seams = cutter.cut()
    islands = cutter.islands(seams)

    # ---- flatten every island ------------------------------------------
    corner_uv = {}          # (face, vertex) -> (u,v)   island-local corner UV
    chart_uv = {}           # island index -> {chart vertex: uv}
    charts = []
    tot_energy = 0.0
    worst_angle = 0.0
    worst_area = 0.0
    n_flipped = 0
    area3_tot = 0.0
    area_uv_tot = 0.0
    for k, isl in enumerate(islands):
        ch = Chart(mesh, isl, seams)
        uv, cvs = LSCM(mesh, isl, ch).solve()
        lut = {cv: uv[i] for i, cv in enumerate(cvs)}
        chart_uv[k] = lut
        for i, (f, v) in enumerate(ch.corners):
            corner_uv[(f, v)] = lut[ch.cvert[i]]
        mt = island_metrics(mesh, isl, corner_uv)
        tot_energy += mt["energy"]
        worst_angle = max(worst_angle, mt["angle_err"])
        worst_area = max(worst_area, mt["area_dist"])
        n_flipped += mt["flipped"]
        area3_tot += mt["total_area_3d"]
        area_uv_tot += mt["total_area_uv"]
        charts.append((isl, ch, lut, cvs))

    # ---- per-corner UVs, which is what a cut mesh actually needs --------
    # `uv` (documented format) holds one UV per input vertex; at a seam vertex
    # several UVs legitimately coexist, so `uv_faces` carries the exact
    # per-triangle-corner UVs used for rendering, and `severed` lists the extra
    # copies.  Vertices away from seams have a single UV everywhere, which is
    # the "shared vertex identity" invariant.
    uv_out = [None] * n_in
    severed = []
    canon_vertex_entries = defaultdict(list)   # canonical vertex -> [(k, uv)]
    for k, (isl, ch, lut, cvs) in enumerate(charts):
        seen_cv = set()
        for i, (f, v) in enumerate(ch.corners):
            cv = ch.cvert[i]
            if cv in seen_cv:
                continue
            seen_cv.add(cv)
            mv = ch.chart_mesh_vertex(cv)
            canon_vertex_entries[mv].append((k, lut[cv]))

    for wv in range(mesh.nv):
        entries = canon_vertex_entries.get(wv, [])
        if not entries:
            continue
        entries.sort(key=lambda t: t[0])
        primary = entries[0][1]
        for inp in mesh.members[wv]:
            uv_out[inp] = [primary[0], primary[1]]
        for k, uv in entries[1:]:
            for inp in mesh.members[wv]:
                severed.append([inp, uv[0], uv[1], k])

    # input vertices that no face references (isolated) get the origin
    for i in range(n_in):
        if uv_out[i] is None:
            uv_out[i] = [0.0, 0.0]

    # per input triangle, the UV of each of its three corners.  Looked up by
    # vertex identity, not by slot, because the canonical face order is rotated
    # (and possibly reversed) relative to the input triangle order.
    corner_of_tri = {}                          # input triangle -> list of uv
    for k, (isl, ch, lut, cvs) in enumerate(charts):
        for f in isl:
            ti = mesh.fsrc[f]
            uvs = []
            for v in tris[ti]:
                cv = mesh.gid_of_input[v]
                corner = ch.cvert[ch.slot_of[(f, cv)]]
                uvs.append(lut[corner])
            corner_of_tri[ti] = uvs
    uv_faces = [corner_of_tri.get(ti, [[0.0, 0.0]] * 3)
                for ti in range(len(tris))]

    # ---- report seams in input indices ---------------------------------
    seams_in = sorted({
        tuple(sorted((mesh.members[u][0], mesh.members[v][0])))
        for u, v in seams
    })
    paths_canon = seam_paths(seams)
    paths_in = [[mesh.members[v][0] for v in p] for p in paths_canon]

    islands_in = sorted({mesh.fsrc[f] for isl in islands for f in isl})
    island_faces = sorted(
        [min(mesh.fsrc[f] for f in isl) for isl in islands]
    )

    # energy is already area-normalised per island; area-weight the islands
    energy = (tot_energy / area3_tot) if area3_tot > 1e-18 else 0.0

    # ---- digest: geometry-keyed so reindexing cannot change it ----------
    seam_geo = sorted(tuple(sorted((_poskey(mesh.wpos[u]), _poskey(mesh.wpos[v]))))
                      for u, v in seams)
    island_geo = []
    for isl in islands:
        sig = sorted(_poskey(mesh.wpos[v]) for f in isl for v in mesh.faces[f])
        island_geo.append("|".join(sig))
    uv_geo = []
    for wv in range(mesh.nv):
        entries = canon_vertex_entries.get(wv, [])
        if not entries:
            continue
        entries.sort(key=lambda t: t[0])
        u, v = entries[0][1]
        uv_geo.append((_poskey(mesh.wpos[wv]), u, v))

    out = {
        "uv": [[_clean(u), _clean(v)] for u, v in uv_out],
        "uv_faces": [[[_clean(u), _clean(v)] for u, v in tri] for tri in uv_faces],
        "seams": [list(e) for e in seams_in],
        "seam_paths": paths_in,
        "islands": len(islands),
        "island_faces": island_faces,
        "energy": _clean(energy),
        "angle_err": _clean(worst_angle),
        "area_dist": _clean(worst_area),
        "flipped": n_flipped,
        "severed": sorted(severed, key=lambda r: (r[0], r[3], r[1], r[2])),
        "stats": {
            "vertices": mesh.nv,
            "faces": mesh.nf,
            "degenerate_faces": len(mesh.degenerate),
            "seam_edges": len(seams_in),
            "area_3d": _clean(area3_tot),
            "area_uv": _clean(area_uv_tot),
        },
    }
    out["digest"] = make_digest(seam_geo, island_geo, uv_geo)
    return out


def _clean(x):
    """Round away floating noise so output is byte-stable."""
    if x is None:
        return 0.0
    if not math.isfinite(x):
        return 0.0
    v = round(x, 10)
    return 0.0 if v == 0.0 else v


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: unwrap.py mesh.json output.json\n")
        return 2
    with open(argv[1]) as fh:
        mesh = json.load(fh)
    pos = mesh["pos"]
    tris = mesh["tris"]
    res = unwrap(pos, tris)
    with open(argv[2], "w") as fh:
        json.dump(res, fh, sort_keys=True)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))

