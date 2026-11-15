#!/usr/bin/env python3
"""Deterministic particle simulation + stable depth-sorted premultiplied-alpha
compositing.

    python3 sim.py req.json out.json

Design
------
* State (positions / velocities / depth layers) lives in Q30 fixed point.  A
  particle update is a pure function of that particle's own integers, so the
  update batch partition (chunk size, worker count) can never change a single
  component.
* Particle/particle collisions are resolved as a Jacobi (gather) pass:
  candidate pairs come from a deterministic uniform grid, are canonicalised to
  ``(i, j)`` with ``i < j``, de-duplicated and index-sorted, and every pair's
  integer delta is *added* into a per-particle accumulator.  Integer addition
  is associative and commutative, so tiling the broad phase or reducing the
  narrow phase over any number of shards reproduces the same accumulators.
* Rendering sorts visible particles by view-space depth descending with a
  deterministic tiebreak (particle id ascending), then composites back to front
  with premultiplied alpha.  Draw batching only groups consecutive samples; it
  never reorders them, so the frame is batch-invariant as well.

Output: final ``traj`` / ``vel`` / ``blend`` / ``order`` / ``digest`` plus a
``frames`` list carrying per-frame particle counts, sort keys and blend
statistics.
"""

import hashlib
import json
import math
import random
import struct
import sys
from concurrent.futures import ThreadPoolExecutor

# --------------------------------------------------------------------------- #
# fixed point
# --------------------------------------------------------------------------- #
FP = 30
ONE = 1 << FP
HALF = 1 << (FP - 1)


def f2i(x):
    """float -> Q30 (round half up, deterministic)."""
    return int(math.floor(x * ONE + 0.5))


def i2f(v):
    """Q30 -> float.  Exact while |v| < 2**53 (ONE is a power of two)."""
    return v / ONE


def fmul(a, b):
    """Q30 * Q30 -> Q30, round half up."""
    return (a * b + HALF) >> FP


def fdiv_q30(a, b):
    """(a / b) in Q30 with truncation towards zero (symmetric, no floor bias)."""
    q = (abs(a) << FP) // abs(b)
    return q if (a >= 0) == (b >= 0) else -q


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _add4(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2], a[3] + b[3])


# --------------------------------------------------------------------------- #
# chunked / threaded execution
# --------------------------------------------------------------------------- #
class Exec:
    """Chunk + thread scheduler.

    ``threads`` / ``chunk`` only decide *how* work is scheduled.  Every task is
    a pure function of the fixed-point state and every reduction merges
    integers under an index/particle key, so a scheduling change is
    unobservable in the produced frame.

    ``scramble`` additionally permutes the *dispatch order* of the chunks and
    reverses their result merge order.  That is a deliberately adversarial
    schedule: it makes the workers execute the same work in a different
    sequence and the reducer fold partial results in a different sequence.  If
    any reduction depended on floating-point accumulation order the results
    would drift; with integer accumulators they cannot.
    """

    __slots__ = ("threads", "chunk", "pool", "scramble")

    def __init__(self, threads, chunk, scramble=False):
        self.threads = max(1, int(threads))
        self.chunk = max(1, int(chunk))
        self.scramble = bool(scramble)
        self.pool = (ThreadPoolExecutor(max_workers=self.threads)
                     if self.threads > 1 else None)

    def chunks(self, n):
        out = []
        s = 0
        while s < n:
            e = min(s + self.chunk, n)
            out.append((s, e))
            s = e
        return out

    def _dispatch(self, parts):
        """Chunk list in dispatch order (scrambled if requested)."""
        if not self.scramble:
            return parts
        out = parts[::-1]
        # interleave from both ends: a different order than sequential
        return out[1::2] + out[0::2]

    def map_index(self, n, fn):
        """[fn(i) for i in range(n)], merged back in index order."""
        if n <= 0:
            return []
        parts = self.chunks(n)
        if self.pool is None and not self.scramble:
            return [fn(i) for i in range(n)]
        out = [None] * n

        def work(se):
            s, e = se
            return s, [fn(i) for i in range(s, e)]

        if self.pool is None:
            # single worker, but still execute the chunks in scrambled order
            for s, vals in map(work, self._dispatch(parts)):
                out[s:s + len(vals)] = vals
            return out
        for s, vals in self.pool.map(work, self._dispatch(parts)):
            out[s:s + len(vals)] = vals
        return out

    def reduce_index(self, n, fn, merge):
        """{key: merge(...)} over fn(i) dicts; integer sums are tiling free."""
        if n <= 0:
            return {}
        parts = self.chunks(n)

        def work(se):
            s, e = se
            loc = {}
            for k in range(s, e):
                item = fn(k)
                if not item:
                    continue
                for key, val in item.items():
                    loc[key] = merge(loc[key], val) if key in loc else val
            return loc

        if self.pool is None and not self.scramble:
            return work((0, n))
        acc = {}
        results = (map(work, self._dispatch(parts)) if self.pool is None
                   else self.pool.map(work, self._dispatch(parts)))
        for loc in results:
            for key, val in loc.items():
                acc[key] = merge(acc[key], val) if key in acc else val
        return acc

    def close(self):
        if self.pool is not None:
            self.pool.shutdown(wait=True)
            self.pool = None


# --------------------------------------------------------------------------- #
# premultiplied alpha compositing (shared with the analytic check)
# --------------------------------------------------------------------------- #
def composite(order, alpha, premul, batch_size=16):
    """Back-to-front premultiplied ``over`` composite.

    ``order``   draw order, far -> near (list of particle indices)
    ``alpha``   per-particle straight alpha
    ``premul``  per-particle premultiplied RGB

    Returns ``[r, g, b, a, coverage]``.  ``batch_size`` only groups consecutive
    samples; consecutive groups are still applied in order, so the result is
    independent of how the draw list is batched.
    """
    r = g = b = a = 0.0
    not_a = 1.0
    batch = max(1, int(batch_size))
    for start in range(0, len(order), batch):
        for idx in order[start:start + batch]:
            sr, sg, sb = premul[idx]
            sa = alpha[idx]
            ia = 1.0 - sa
            # dst = src over dst
            r = sr + r * ia
            g = sg + g * ia
            b = sb + b * ia
            a = sa + a * ia
            not_a *= ia
    return [r, g, b, a, 1.0 - not_a]


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
CAMERA = {
    "eye": [32.0, 32.0, -60.0],   # camera position in world space
    "tilt": 0.0,                  # forward = (sin tilt, 0, cos tilt)
    "half": [26.0, 40.0],         # half extents of the view rectangle
    "near": 0.0,
    "far": 80.0,
}

DEFAULTS = {
    "n": 64,
    "steps": 300,
    "seed": 7,
    "force": [0.0, -9.8],
    "bounds": [0.0, 64.0],        # x / y world bounds
    "z_bounds": [0.0, 16.0],      # depth-layer bounds
    "threads": 4,
    "chunk": 16,
    "draw_batch": 16,
    "radius": 1.0,
    "restitution": 0.5,           # wall bounce
    "collision_restitution": 0.0, # particle/particle
    "depth_quant_bits": 0,        # > 0 coarsens the sort key (injected ties)
    "dup_layers": 0,              # > 0 forces cycled depth layers (exact ties)
    "collision_iterations": 4,    # deterministic Jacobi rounds per step
    "scramble": False,            # adversarial worker dispatch order
    "dt": 1.0 / 60.0,
    "particles": None,            # explicit particle list (overrides n)
    "field": None,                # optional deterministic force field
}


def _config(req):
    cfg = dict(DEFAULTS)
    cfg.update({k: v for k, v in req.items() if k != "camera"})
    cam = dict(CAMERA)
    cam.update(req.get("camera") or {})
    cfg["camera"] = cam
    return cfg


# --------------------------------------------------------------------------- #
# simulation
# --------------------------------------------------------------------------- #
def simulate(req):
    """Run the simulation.

    Returns ``(out, frames)`` where ``frames`` holds the exact fixed-point
    state of every frame (used by the verification harness).
    """
    cfg = _config(req)
    steps = int(cfg["steps"])

    dt_i = f2i(float(cfg["dt"]))
    gx = fmul(f2i(float(cfg["force"][0])), dt_i)
    gy = fmul(f2i(float(cfg["force"][1])), dt_i)

    wall_rest = -f2i(float(cfg["restitution"]))
    pair_rest = f2i(float(cfg["collision_restitution"]))
    impulse_k = fmul(ONE + pair_rest, f2i(0.5))       # (1 + e) / 2
    radius_i = max(0, f2i(float(cfg["radius"])))
    diam = 2 * radius_i
    diam2 = diam * diam
    cell = diam if diam > 0 else ONE
    cap_pos = radius_i if radius_i > 0 else ONE        # max correction / frame
    cap_vel = f2i(8.0)                                 # max |dv| / frame
    cap_acc = f2i(2.0)                                 # max |field accel| / frame
    iters = max(1, int(cfg["collision_iterations"]))

    bx0, bx1 = float(cfg["bounds"][0]), float(cfg["bounds"][1])
    z0, z1 = float(cfg["z_bounds"][0]), float(cfg["z_bounds"][1])
    bx0i, bx1i, bz0i, bz1i = f2i(bx0), f2i(bx1), f2i(z0), f2i(z1)

    # deterministic optional force field: a = strength * ((cy - y), (x - cx)) / s
    field = cfg.get("field") or {}
    ftype = str(field.get("type", "none"))
    f_str = f2i(float(field.get("strength", 0.0)))
    f_scale = f2i(float(field.get("scale", 32.0)))
    f_cx, f_cy = f2i(float(field.get("center", [32.0, 32.0])[0])), \
        f2i(float(field.get("center", [32.0, 32.0])[1]))
    field_on = ftype == "swirl" and f_str != 0 and f_scale != 0

    # camera basis (fixed point, kept out of the float world entirely)
    cam = cfg["camera"]
    ex, ey, ez = (f2i(float(v)) for v in cam["eye"])
    sin_t = f2i(math.sin(float(cam["tilt"])))
    cos_t = f2i(math.cos(float(cam["tilt"])))
    half_w = f2i(float(cam["half"][0]))
    half_h = f2i(float(cam["half"][1]))
    near = f2i(float(cam["near"]))
    far = f2i(float(cam["far"]))
    dq = max(0, int(cfg["depth_quant_bits"]))

    # ---- initial state ---------------------------------------------------- #
    px, py, pz, vx, vy, vz = [], [], [], [], [], []
    alpha, cr, cg, cb = [], [], [], []

    explicit = cfg.get("particles")
    if explicit:
        for p in explicit:
            px.append(f2i(float(p.get("x", 0.0))))
            py.append(f2i(float(p.get("y", 0.0))))
            pz.append(f2i(float(p.get("z", 0.0))))
            vx.append(f2i(float(p.get("vx", 0.0))))
            vy.append(f2i(float(p.get("vy", 0.0))))
            vz.append(f2i(float(p.get("vz", 0.0))))
            alpha.append(float(p.get("a", 1.0)))
            c = p.get("color", [1.0, 1.0, 1.0])
            cr.append(float(c[0]))
            cg.append(float(c[1]))
            cb.append(float(c[2]))
    else:
        rng = random.Random(int(cfg["seed"]))
        dup = max(0, int(cfg["dup_layers"]))
        for i in range(int(cfg["n"])):
            px.append(f2i(rng.uniform(bx0, bx1)))
            py.append(f2i(rng.uniform(bx0, bx1)))
            if dup > 0:
                # exact duplicate depth layers -> guaranteed sort-key ties
                if dup > 1:
                    pz.append(bz0i + ((bz1i - bz0i) * (i % dup)) // (dup - 1))
                else:
                    pz.append(bz0i)
                vz.append(0)
            else:
                pz.append(f2i(rng.uniform(z0, z1)))
                vz.append(f2i(rng.uniform(-1.0, 1.0)))
            vx.append(f2i(rng.uniform(-3.0, 3.0)))
            vy.append(f2i(rng.uniform(-3.0, 3.0)))
            alpha.append(rng.uniform(0.35, 1.0))
            cr.append(rng.uniform(0.0, 1.0))
            cg.append(rng.uniform(0.0, 1.0))
            cb.append(rng.uniform(0.0, 1.0))

    n = len(px)
    premul = [(cr[i] * alpha[i], cg[i] * alpha[i], cb[i] * alpha[i])
              for i in range(n)]

    ex_ = Exec(cfg["threads"], cfg["chunk"], bool(cfg.get("scramble")))
    state_hash = hashlib.sha256()
    render_hash = hashlib.sha256()
    frames = []
    frame_summaries = []

    # ---- per-particle update (pure, scheduling free) ---------------------- #
    def integrate_one(i):
        axi = gx
        ayi = gy
        if field_on:
            sx = px[i] - f_cx
            sy = py[i] - f_cy
            # a = strength * (cy - y, x - cx) / scale   (rigid rotation field)
            axi += _clamp(fdiv_q30(fmul(f_str, -sy), f_scale), -cap_acc, cap_acc)
            ayi += _clamp(fdiv_q30(fmul(f_str, sx), f_scale), -cap_acc, cap_acc)
        vxi = vx[i] + axi
        vyi = vy[i] + ayi
        vzi = vz[i]
        pxi = px[i] + fmul(vxi, dt_i)
        pyi = py[i] + fmul(vyi, dt_i)
        pzi = pz[i] + fmul(vzi, dt_i)
        if pxi < bx0i:
            pxi = bx0i
            vxi = fmul(vxi, wall_rest)
        elif pxi > bx1i:
            pxi = bx1i
            vxi = fmul(vxi, wall_rest)
        if pyi < bx0i:
            pyi = bx0i
            vyi = fmul(vyi, wall_rest)
        elif pyi > bx1i:
            pyi = bx1i
            vyi = fmul(vyi, wall_rest)
        if pzi < bz0i:
            pzi = bz0i
            vzi = fmul(vzi, wall_rest)
        elif pzi > bz1i:
            pzi = bz1i
            vzi = fmul(vzi, wall_rest)
        return pxi, pyi, pzi, vxi, vyi, vzi

    # ---- deterministic particle/particle collisions (Jacobi) -------------- #
    def wall_fix(i):
        """Per-particle wall constraint, used after each collision relaxation.

        Pure function of the particle's own integers, so it does not affect
        scheduling invariance.
        """
        vxi, vyi = vx[i], vy[i]
        if px[i] < bx0i:
            px[i] = bx0i
            vxi = fmul(vxi, wall_rest)
        elif px[i] > bx1i:
            px[i] = bx1i
            vxi = fmul(vxi, wall_rest)
        if py[i] < bx0i:
            py[i] = bx0i
            vyi = fmul(vyi, wall_rest)
        elif py[i] > bx1i:
            py[i] = bx1i
            vyi = fmul(vyi, wall_rest)
        vx[i], vy[i] = vxi, vyi
        return i

    def collide():
        """Deterministic Jacobi relaxation of particle overlaps.

        Each round is a gather pass: every delta is computed from the *same*
        pre-round state and summed with **integers**.  Both the tiling of the
        broad phase and the sharding of the narrow phase are therefore
        irrelevant, and ``collision_iterations`` rounds are just a fixed,
        deterministic schedule.

        Returns ``(candidates, contacts, capped)`` accumulated over all rounds.
        """
        if diam == 0:
            return 0, 0, 0

        offsets = ((0, 0), (1, -1), (1, 0), (1, 1), (0, 1))
        total_cand = total_hits = total_capped = 0

        for _ in range(iters):
            # broad phase: deterministic uniform grid, cell = collision diameter
            cells = ex_.map_index(n, lambda i: (px[i] // cell, py[i] // cell, i))
            buckets = {}
            for cx, cy, i in cells:
                buckets.setdefault((cx, cy), []).append(i)
            for ids in buckets.values():
                ids.sort()
            keys = sorted(buckets)

            def candidates(k):
                cx, cy = keys[k]
                here = buckets[(cx, cy)]
                out = []
                for ox, oy in offsets:
                    other = buckets.get((cx + ox, cy + oy))
                    if other is None:
                        continue
                    for ii in here:
                        for jj in other:
                            if ii == jj:
                                continue
                            if ox == 0 and oy == 0 and jj <= ii:
                                continue
                            out.append((ii, jj) if ii < jj else (jj, ii))
                return out

            parts = ex_.map_index(len(keys), candidates)
            pairs = sorted(set(p for part in parts for p in part))
            total_cand += len(pairs)
            if pairs:
                def pair_delta(k):
                    i, j = pairs[k]
                    dx = px[j] - px[i]
                    dy = py[j] - py[i]
                    d2 = dx * dx + dy * dy
                    if d2 >= diam2:
                        return {}
                    if d2 == 0:
                        # coincident centres: direction derived from ids
                        nx = ONE if ((i * 31 + j * 17) & 1) == 0 else -ONE
                        ny = 0
                        dist = 0
                    else:
                        dist = math.isqrt(d2)
                        nx = _clamp(fdiv_q30(dx, dist), -ONE, ONE)
                        ny = _clamp(fdiv_q30(dy, dist), -ONE, ONE)
                    corr = (diam - dist) >> 1
                    vn = fmul(vx[j] - vx[i], nx) + fmul(vy[j] - vy[i], ny)
                    imp = fmul(-vn, impulse_k) if vn < 0 else 0
                    dpx = fmul(corr, nx)
                    dpy = fmul(corr, ny)
                    dvx = fmul(imp, nx)
                    dvy = fmul(imp, ny)
                    return {i: (-dpx, -dpy, -dvx, -dvy),
                            j: (dpx, dpy, dvx, dvy)}

                deltas = ex_.reduce_index(len(pairs), pair_delta, _add4)
                total_hits += len(deltas)
                for i, (dpx, dpy, dvx, dvy) in deltas.items():
                    cxx = _clamp(dpx, -cap_pos, cap_pos)
                    cyy = _clamp(dpy, -cap_pos, cap_pos)
                    uvx = _clamp(dvx, -cap_vel, cap_vel)
                    uvy = _clamp(dvy, -cap_vel, cap_vel)
                    if (cxx, cyy, uvx, uvy) != (dpx, dpy, dvx, dvy):
                        total_capped += 1
                    px[i] += cxx
                    py[i] += cyy
                    vx[i] += uvx
                    vy[i] += uvy
            # keep corrections inside the world (pure per-particle, order free)
            ex_.map_index(n, wall_fix)

        return total_cand, total_hits, total_capped

    # ---- view transform / culling / stable depth sort --------------------- #
    def view_one(i):
        dx = px[i] - ex
        dz = pz[i] - ez
        depth = fmul(dx, sin_t) + fmul(dz, cos_t)
        if depth < near or depth > far:
            return None
        sx = fmul(dx, cos_t) - fmul(dz, sin_t)
        if sx < -half_w or sx > half_w:
            return None
        sy = py[i] - ey
        if sy < -half_h or sy > half_h:
            return None
        key = (depth >> dq) if dq > 0 else depth
        return key, i

    def draw():
        vis = [r for r in ex_.map_index(n, view_one) if r is not None]
        # depth descending, then particle id ascending -> total, stable order
        vis.sort(key=lambda r: (-r[0], r[1]))
        order = [r[1] for r in vis]
        blend = composite(order, alpha, premul, cfg["draw_batch"])

        tie_pairs = 0
        tie_groups = 0
        max_group = 1
        k = 0
        while k < len(vis):
            j = k + 1
            while j < len(vis) and vis[j][0] == vis[k][0]:
                j += 1
            run = j - k
            if run > 1:
                tie_groups += 1
                tie_pairs += run * (run - 1) // 2
                if run > max_group:
                    max_group = run
            k = j

        return vis, order, blend, tie_pairs, tie_groups, max_group

    # ---- main loop -------------------------------------------------------- #
    for step in range(steps):
        res = ex_.map_index(n, integrate_one)
        px = [r[0] for r in res]
        py = [r[1] for r in res]
        pz = [r[2] for r in res]
        vx = [r[3] for r in res]
        vy = [r[4] for r in res]
        vz = [r[5] for r in res]
        n_pairs, n_hits, n_capped = collide()

        vis, order, blend, tie_pairs, tie_groups, max_group = draw()
        r, g, b, a, cov = blend

        sh = hashlib.sha256()
        sh.update(struct.pack("<%dq" % (6 * n),
                              *[v for i in range(n)
                                for v in (px[i], py[i], pz[i],
                                          vx[i], vy[i], vz[i])]))
        state_digest = sh.hexdigest()
        state_hash.update(sh.digest())

        rh = hashlib.sha256()
        rh.update(struct.pack("<%dq" % (2 * len(vis)),
                              *[v for key, idx in vis for v in (key, idx)]))
        rh.update(struct.pack("<qqqq", int(round(r * 1e9)), int(round(g * 1e9)),
                              int(round(b * 1e9)), int(round(a * 1e9))))
        render_digest = rh.hexdigest()
        render_hash.update(rh.digest())

        frame_summaries.append({
            "step": step + 1,
            "state_digest": state_digest,
            "render_digest": render_digest,
            "counts": {
                "total": n,
                "visible": len(vis),
                "culled": n - len(vis),
                "updated": n,
                "collision_candidates": n_pairs,
                "collision_contacts": n_hits,
                "collision_capped": n_capped,
            },
            "sort_keys": [[key, idx] for key, idx in vis],
            "order": order,
            "blend": [r, g, b, a],
            "blend_stats": {
                "layers": len(vis),
                "coverage": cov,
                "alpha": a,
                "sum_alpha": sum(alpha[i] for i in order),
                "tie_pairs": tie_pairs,
                "tie_groups": tie_groups,
                "max_tie_group": max_group,
                "draw_batches": ((len(order) + max(1, int(cfg["draw_batch"])) - 1)
                                 // max(1, int(cfg["draw_batch"]))),
                "depth_max": i2f(vis[0][0] << dq) if vis else 0.0,
                "depth_min": i2f(vis[-1][0] << dq) if vis else 0.0,
            },
        })
        frames.append((list(px), list(py), list(pz), list(vx), list(vy), list(vz)))

    ex_.close()

    last = frame_summaries[-1] if frame_summaries else None
    out = {
        "schema": "r06.deterministic.v1",
        "n": n,
        "steps": steps,
        "seed": int(cfg["seed"]),
        "fixed_point_bits": FP,
        "threads": cfg["threads"],
        "chunk": cfg["chunk"],
        "draw_batch": cfg["draw_batch"],
        "traj": [[i2f(px[i]), i2f(py[i])] for i in range(n)],
        "vel": [[i2f(vx[i]), i2f(vy[i])] for i in range(n)],
        "depth": [i2f(pz[i]) for i in range(n)],
        "alpha": list(alpha),
        "color": [[cr[i], cg[i], cb[i]] for i in range(n)],
        "blend": last["blend"] if last else [0.0, 0.0, 0.0, 0.0],
        "order": last["order"] if last else [],
        "counts": last["counts"] if last else
                  {"total": n, "visible": 0, "culled": 0, "updated": 0,
                   "collision_candidates": 0, "collision_contacts": 0,
                   "collision_capped": 0},
        "blend_stats": last["blend_stats"] if last else {},
        "frames": frame_summaries,
        "frame_count": len(frame_summaries),
        "digest": state_hash.hexdigest(),
        "render_digest": render_hash.hexdigest(),
        "final_digest": (last["state_digest"] + last["render_digest"])
                        if last else "",
    }
    return out, frames


def run(req):
    """Convenience wrapper returning the JSON-ready result only."""
    return simulate(req)[0]


def main(argv):
    if len(argv) != 3:
        sys.stderr.write("usage: python3 sim.py req.json out.json\n")
        return 2
    with open(argv[1]) as f:
        req = json.load(f)
    out = run(req)
    with open(argv[2], "w") as f:
        json.dump(out, f, separators=(",", ":"), sort_keys=True)
        f.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
