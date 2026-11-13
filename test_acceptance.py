"""Acceptance suite for the projective EWA texture pipeline (pure stdlib).

Covers:
  A. analytic UV derivatives on an obliquely projected plane (rel. err 1e-3)
  B. grazing-angle checker ROI vs independent area integration (>=25% better
     than the single-point bilinear baseline)
  C. diagonal-swap stability of one plane (linear color diff <= 0.01)
  D. near-clip intersection edges and degenerate-primitive rejection
  E. periodic (repeat) wrap boundary invariance
  F. two camera paths, re-running C/D/E, with derivative + mip diagnostics
  G. explicit magnification / minification fallbacks
Run: python3 test_acceptance.py
"""
import json
import math

import texture

TEX_N = 32
ANISO_TEX = 64                 # high-frequency texture for the ROI test


# --------------------------------------------------------------------------
# Textures
# --------------------------------------------------------------------------
def checker_texture(n, soft=0.0):
    """Hard 0/1 checker; soft>0 gives a smooth (bandlimited) sinusoid checker."""
    tex = [[0.0] * n for _ in range(n)]
    if soft <= 0.0:
        for j in range(n):
            for i in range(n):
                tex[j][i] = float(((i // (n // 2)) + (j // (n // 2))) % 2)
        return tex
    for j in range(n):
        for i in range(n):
            u = (i + 0.5) / n
            v = (j + 0.5) / n
            s = math.sin(2 * math.pi * u) * math.sin(2 * math.pi * v)
            tex[j][i] = 0.5 + 0.5 * math.tanh(s / soft)
    return tex


# --------------------------------------------------------------------------
# Pinhole camera: camera-space (X right, Y up, -Z forward), near n, focal f
# Screen: sx = W/2 + f*X/(-Z) * P ; P = pixels-per-unit at the image plane
# --------------------------------------------------------------------------
class Camera:
    def __init__(self, eye, target, f=1.0, near=0.1, w=256, h=256, ppu=128.0):
        self.eye = tuple(eye)
        self.near = near
        self.f = f
        self.w = w
        self.h = h
        self.ppu = ppu
        ez = self._norm(self._sub(target, eye))           # forward (-Z axis)
        up = (0.0, 1.0, 0.0)
        ex = self._norm(self._cross(ez, up))              # right
        ey = self._cross(ex, ez)                          # true up
        self.ex, self.ey, self.ez = ex, ey, ez

    @staticmethod
    def _sub(a, b):
        return tuple(a[i] - b[i] for i in range(3))

    @staticmethod
    def _cross(a, b):
        return (a[1] * b[2] - a[2] * b[1],
                a[2] * b[0] - a[0] * b[2],
                a[0] * b[1] - a[1] * b[0])

    @staticmethod
    def _dot(a, b):
        return sum(a[i] * b[i] for i in range(3))

    @classmethod
    def _norm(cls, v):
        d = math.sqrt(cls._dot(v, v))
        return tuple(x / d for x in v)

    def to_camera(self, p):
        r = self._sub(p, self.eye)
        return (self._dot(r, self.ex), self._dot(r, self.ey),
                -self._dot(r, self.ez))

    def project(self, p, uv):
        """World point + (u,v) -> screen vertex with perspective w = -Z/n."""
        X, Y, Z = self.to_camera(p)
        depth = -Z
        sx = self.w / 2.0 + self.f * X / depth * self.ppu
        sy = self.h / 2.0 - self.f * Y / depth * self.ppu
        return {"x": sx, "y": sy, "w": depth / self.near,
                "u": uv[0], "v": uv[1], "depth": depth}

    def ray_uv(self, plane, px, py, center=True):
        """Analytic ray/planar-surface intersection -> continuous UV + grads.

        px, py name a pixel; its sample point is the pixel center (px+0.5).
        plane: dict(origin p0, basis (eu, ev)) in world space.
        Returns (u, v, du/dx, du/dy, dv/dx, dv/dy) or None behind camera.
        Rays use the *unnormalized* direction (ndc_x, ndc_y, -1) so the
        screen-space chain rule stays exact.
        """
        sx = px + (0.5 if center else 0.0)
        sy = py + (0.5 if center else 0.0)
        ndc_x = (sx - self.w / 2.0) / self.ppu / self.f
        ndc_y = -(sy - self.h / 2.0) / self.ppu / self.f

        def cam_vec_to_world(X, Y, Z):
            # inverse of to_camera: r = X*ex + Y*ey - Z*ez
            return tuple(X * self.ex[i] + Y * self.ey[i]
                         - Z * self.ez[i] for i in range(3))

        o = self.eye
        # view ray in camera space is (ndc_x, ndc_y, -1) (Z axis looks along -Z)
        d = cam_vec_to_world(ndc_x, ndc_y, -1.0)
        p0 = plane["origin"]
        eu, ev = plane["eu"], plane["ev"]
        nrm = self._norm(self._cross(eu, ev))
        denom = self._dot(d, nrm)
        if abs(denom) < 1e-12:
            return None
        t = self._dot(self._sub(p0, o), nrm) / denom
        if t <= 0.0:
            return None
        hit = tuple(o[i] + t * d[i] for i in range(3))
        r = self._sub(hit, p0)
        guu = self._dot(eu, eu)
        guv = self._dot(eu, ev)
        gvv = self._dot(ev, ev)
        det = guu * gvv - guv * guv
        a = (gvv * self._dot(r, eu) - guv * self._dot(r, ev)) / det
        b = (guu * self._dot(r, ev) - guv * self._dot(r, eu)) / det
        # texture UV repeats `tiles` times across the plane
        tiles = plane.get("tiles", 1.0)
        u, v = a * tiles, b * tiles

        # Screen chain rule: hit = eye + t*d with n.(hit-p0)=0.
        # Differentiating: n.(t*dd/ds + dt/ds*d)=0
        # => dt/ds = -t*(n.dd/ds)/(n.d), then dhit/ds = t*dd + dt*d.
        # ndc_x = sx/ppu  => dd/d(sx) = ex/ppu ;
        # ndc_y = -sy/ppu => dd/d(sy) = -ey/ppu (screen y flips camera Y).
        ddx_c = (1.0 / self.ppu / self.f, 0.0, 0.0)
        ddy_c = (0.0, -1.0 / self.ppu / self.f, 0.0)

        def grad_along(dd_c):
            dd_w = cam_vec_to_world(dd_c[0], dd_c[1], dd_c[2])
            dt = -t * self._dot(nrm, dd_w) / denom
            dhit = tuple(dd_w[i] * t + d[i] * dt for i in range(3))
            da = (gvv * self._dot(dhit, eu) - guv * self._dot(dhit, ev)) / det
            db = (guu * self._dot(dhit, ev) - guv * self._dot(dhit, eu)) / det
            return da * tiles, db * tiles

        du_dx, dv_dx = grad_along(ddx_c)
        du_dy, dv_dy = grad_along(ddy_c)
        return (u, v, du_dx, du_dy, dv_dx, dv_dy)


# --------------------------------------------------------------------------
# Plane + mesh builders
# --------------------------------------------------------------------------
def make_plane(center=(0.0, 0.0, -3.0), width=4.0, height=4.0,
               rot_z=0.0, tiles=1.0):
    """Tilted plane spanned by orthogonal basis vectors in world space."""
    c, s = math.cos(rot_z), math.sin(rot_z)
    base_u = (c, 0.0, s)
    base_v = (0.0, 1.0, 0.0)
    eu = tuple(width * 0.5 * x for x in base_u)
    ev = tuple(height * 0.5 * x for x in base_v)
    origin = tuple(center[i] - 0.5 * eu[i] - 0.5 * ev[i] for i in range(3))

    def point(a, b):
        # a,b in [0,1] across the plane; UV repeats `tiles` times
        return tuple(origin[i] + a * eu[i] + b * ev[i] for i in range(3)), \
            (a * tiles, b * tiles)
    return {"origin": origin, "eu": eu, "ev": ev, "tiles": tiles,
            "point": point}


def grid_mesh(plane, nu, nv):
    """Grid vertices (world, uv) indexed [j][i], i,j in 0..nu / 0..nv."""
    grid = []
    for j in range(nv + 1):
        row = []
        for i in range(nu + 1):
            row.append(plane["point"](i / nu, j / nv))
        grid.append(row)
    return grid


def projected_mesh(cam, grid):
    return [[cam.project(p, uv) for (p, uv) in row] for row in grid]


def tri_diag_a(pgrid, i, j):
    return [pgrid[j][i], pgrid[j][i + 1], pgrid[j + 1][i + 1]]


def tri_diag_b(pgrid, i, j):
    return [pgrid[j][i], pgrid[j + 1][i + 1], pgrid[j + 1][i]]


def cell_tris_a(pgrid, i, j):
    return [[pgrid[j][i], pgrid[j][i + 1], pgrid[j + 1][i]],
            [pgrid[j][i + 1], pgrid[j + 1][i + 1], pgrid[j + 1][i]]]


def cell_tris_b(pgrid, i, j):
    # swapped diagonal of the same quad cell
    return [[pgrid[j][i], pgrid[j][i + 1], pgrid[j + 1][i + 1]],
            [pgrid[j][i], pgrid[j + 1][i + 1], pgrid[j + 1][i]]]


# --------------------------------------------------------------------------
# Independent area integration over a pixel's analytic UV footprint
# --------------------------------------------------------------------------
def checker_value(u, v, tiles):
    """Continuous periodic hard checker in UV space."""
    fu = (u * tiles) % 1.0
    fv = (v * tiles) % 1.0
    return float((int(fu * 2) + int(fv * 2)) % 2)


def area_integral(cam, plane, px, py, sub=48):
    """Average checker value over the pixel quad by deterministic grid."""
    acc = 0.0
    cnt = 0
    for jy in range(sub):
        for ix in range(sub):
            qx = px + (ix + 0.5) / sub
            qy = py + (jy + 0.5) / sub
            hit = cam.ray_uv(plane, qx, qy, center=False)
            if hit is None:
                continue
            u, v = hit[0], hit[1]
            acc += checker_value(u, v, 1.0)
            cnt += 1
    if cnt == 0:
        return None
    return acc / cnt


def rasterize_color(levels, tris, px, py, near=None, point=False):
    """First covering triangle wins; returns sampler result or None."""
    stats = texture.SamplerStats()
    for tri in tris:
        ev = texture.evaluate_sample(tri, px + 0.5, py + 0.5, near)
        if ev is not None:
            col, mip, taps = texture.sample_texture(
                levels, ev["u"], ev["v"], ev["gradient"], stats, point)
            return col, mip, taps, ev
    return None


# --------------------------------------------------------------------------
# Test runner
# --------------------------------------------------------------------------
RESULTS = []


def check(name, ok, detail):
    RESULTS.append((name, ok, detail))
    flag = "PASS" if ok else "FAIL"
    print("[%s] %s -- %s" % (flag, name, detail))


def rel_err(a, b):
    denom = max(abs(a), abs(b), 1e-9)
    return abs(a - b) / denom


def test_analytic_derivatives():
    cam = Camera(eye=(0.6, 1.2, 0.2), target=(0.0, 0.0, -3.0), w=160, h=160)
    plane = make_plane(rot_z=math.radians(28.0), tiles=2.0)
    grid = grid_mesh(plane, 2, 2)
    pgrid = projected_mesh(cam, grid)
    levels = texture.build_mip_chain(checker_texture(TEX_N))
    worst = 0.0
    npts = 0
    for j in range(1, 159, 7):
        for i in range(1, 159, 7):
            hit = cam.ray_uv(plane, i, j)
            if hit is None:
                continue
            raster = rasterize_color(levels,
                                     [tri_diag_a(pgrid, x, y)
                                      for y in range(2) for x in range(2)],
                                     i, j)
            if raster is None:
                continue
            g = raster[3]["gradient"]
            ref = hit[2:6]
            for k in range(4):
                worst = max(worst, rel_err(g[k], ref[k]))
            npts += 1
    check("A. analytic UV derivatives rel.err <= 1e-3",
          worst <= 1e-3,
          "points=%d worst_rel_err=%.3e" % (npts, worst))
    return worst


def test_grazing_roi():
    # Grazing camera at an oblique azimuth: the plane's projected footprint is
    # a long thin ellipse on screen (strong anisotropy), which a single
    # bilinear point cannot integrate.
    az = math.radians(75.0)
    el = math.radians(9.0)
    dist = 4.0
    eye = (dist * math.cos(el) * math.sin(az),
           dist * math.sin(el),
           -3.0 + dist * math.cos(el) * math.cos(az))
    cam = Camera(eye=eye, target=(0.0, 0.0, -3.0), w=200, h=200, ppu=110.0)
    plane = make_plane(rot_z=math.radians(10.0), tiles=5.0)
    grid = grid_mesh(plane, 4, 4)
    pgrid = projected_mesh(cam, grid)
    tex = checker_texture(ANISO_TEX)
    levels = texture.build_mip_chain(tex)
    tris = [tri_diag_a(pgrid, x, y) for y in range(4) for x in range(4)]

    errs_iso, errs_aniso, used, aniso_seen, mip_seen = [], [], 0, 0, 0.0
    ratio_seen = 0.0
    for j in range(10, 190, 2):
        for i in range(10, 190, 2):
            truth = area_integral(cam, plane, i, j, sub=24)
            if truth is None:
                continue
            base = rasterize_color(levels, tris, i, j, point=True)
            full = rasterize_color(levels, tris, i, j)
            if base is None or full is None:
                continue
            used += 1
            errs_iso.append((base[0] - truth) ** 2)
            errs_aniso.append((full[0] - truth) ** 2)
            aniso_seen = max(aniso_seen, full[2])
            mip_seen = max(mip_seen, full[1])
            maj, minr, _ = texture.footprint_axes(full[3]["gradient"])
            ratio_seen = max(ratio_seen, maj / max(minr, 1e-12))
    rmse_base = math.sqrt(sum(errs_iso) / len(errs_iso))
    rmse_aniso = math.sqrt(sum(errs_aniso) / len(errs_aniso))
    reduction = 1.0 - rmse_aniso / rmse_base
    check("B. grazing ROI >=25%% error reduction vs point bilinear",
          reduction >= 0.25,
          "roi=%d rmse_point=%.4f rmse_aniso=%.4f reduction=%.1f%% "
          "max_taps=%d max_mip=%.2f max_axis_ratio=%.1f"
          % (used, rmse_base, rmse_aniso, 100 * reduction,
             aniso_seen, mip_seen, ratio_seen))
    return reduction


def test_diagonal_swap(cam, plane, label, near=None, edge_margin=2.0):
    grid = grid_mesh(plane, 3, 3)
    pgrid = projected_mesh(cam, grid)
    levels = texture.build_mip_chain(checker_texture(TEX_N))
    tris_a = [t for y in range(3) for x in range(3) for t in cell_tris_a(pgrid, x, y)]
    tris_b = [t for y in range(3) for x in range(3) for t in cell_tris_b(pgrid, x, y)]
    max_diff = 0.0
    worst = None
    compared = 0
    diag = {}
    for j in range(0, cam.h):
        for i in range(0, cam.w):
            ra = rasterize_color(levels, tris_a, i, j, near)
            rb = rasterize_color(levels, tris_b, i, j, near)
            if ra is None or rb is None:
                continue
            # interior, non-silhouette pixels: away from every triangle edge
            if min_dist_to_edges(tris_a, i + 0.5, j + 0.5) < edge_margin:
                continue
            compared += 1
            d = abs(ra[0] - rb[0])
            if d > max_diff:
                max_diff = d
                worst = (i, j, ra[1], rb[1], ra[2], rb[2])
            diag.setdefault("mip_a", []).append(ra[1])
            diag.setdefault("taps_a", []).append(ra[2])
    mip_range = (min(diag["mip_a"]), max(diag["mip_a"])) if diag else (0, 0)
    taps_range = (min(diag["taps_a"]), max(diag["taps_a"])) if diag else (0, 0)
    check("C. diagonal-swap color diff <= 0.01 (%s)" % label,
          max_diff <= 0.01,
          "interior=%d max|dC|=%.3e at %s mip=%s taps=%s"
          % (compared, max_diff, worst, mip_range, taps_range))
    return max_diff


def min_dist_to_edges(tris, px, py):
    best = float("inf")
    for tri in tris:
        for k in range(3):
            a = tri[k]
            b = tri[(k + 1) % 3]
            vx, vy = b["x"] - a["x"], b["y"] - a["y"]
            L2 = vx * vx + vy * vy
            if L2 < 1e-12:
                continue
            t = ((px - a["x"]) * vx + (py - a["y"]) * vy) / L2
            t = max(0.0, min(1.0, t))
            qx, qy = a["x"] + t * vx, a["y"] + t * vy
            best = min(best, math.hypot(px - qx, py - qy))
    return best


def test_near_clip_and_degenerate():
    # Screen-space triangle whose third vertex projects with w < near scale,
    # so the near clip cuts a real region of rasterized coverage.
    near = 1.0
    tri = [{"x": 10.0, "y": 10.0, "w": 3.0, "u": 0.0, "v": 0.0},
           {"x": 110.0, "y": 10.0, "w": 3.0, "u": 1.0, "v": 0.0},
           {"x": 60.0, "y": 110.0, "w": 0.4, "u": 0.5, "v": 1.0}]
    kept = cut = violations = 0
    min_depth = float("inf")
    for j in range(10, 110):
        for i in range(10, 110):
            ev_raw = texture.evaluate_sample(tri, i + 0.5, j + 0.5, None)
            ev_clip = texture.evaluate_sample(tri, i + 0.5, j + 0.5, near)
            if ev_raw is not None:
                kept += 1
            if ev_clip is not None:
                cut += 1
                depth = 1.0 / ev_clip["inv_w"]
                min_depth = min(min_depth, depth)
                if depth < near - 1e-9:
                    violations += 1
    ok_clip = cut < kept and violations == 0 and cut > 0

    # Explicit clip polygon: intersect edges (0,2) and (1,2) with depth=near.
    # Intersection vertices are built by interpolating the perspective-correct
    # affine quantities (1/w, u/w, v/w) -- never (u, v) directly -- then
    # recovering w, u, v. This is the independent reference construction.
    inv_w = [1.0 / q["w"] for q in tri]
    u_w = [q["u"] / q["w"] for q in tri]
    v_w = [q["v"] / q["w"] for q in tri]
    limit = 1.0 / near
    e02 = e12 = None
    for ia, ib in ((0, 2), (1, 2)):
        alpha = (limit - inv_w[ia]) / (inv_w[ib] - inv_w[ia])
        q = inv_w[ia] + alpha * (inv_w[ib] - inv_w[ia])
        uwv = u_w[ia] + alpha * (u_w[ib] - u_w[ia])
        vwv = v_w[ia] + alpha * (v_w[ib] - v_w[ia])
        v = {
            "x": tri[ia]["x"] + alpha * (tri[ib]["x"] - tri[ia]["x"]),
            "y": tri[ia]["y"] + alpha * (tri[ib]["y"] - tri[ia]["y"]),
            "w": 1.0 / q,
            "u": uwv / q,
            "v": vwv / q,
        }
        if ia == 0:
            e02 = v
        else:
            e12 = v
    # quad order: far-left (tri0), far-right (tri1), right clip, left clip.
    # Split along diagonal far_l -> e12 into two non-degenerate triangles.
    far_l, far_r = dict(tri[0]), dict(tri[1])
    quad_tris = [[far_l, far_r, e12], [far_l, e12, e02]]
    # sanity: the two triangles must have nonzero, equally-signed area
    _areas = []
    for qt in quad_tris:
        _areas.append(texture._edge(qt[0]["x"], qt[0]["y"],
                                    qt[1]["x"], qt[1]["y"],
                                    qt[2]["x"], qt[2]["y"]))
    assert all(abs(ar) > 1e-9 for ar in _areas) and \
        all((ar > 0) == (_areas[0] > 0) for ar in _areas), _areas
    ref_err = 0.0
    ref_n = 0
    for j in range(12, 38):
        for i in range(20, 100):
            ev_clip = texture.evaluate_sample(tri, i + 0.5, j + 0.5, near)
            if ev_clip is None:
                continue
            ref = None
            for qt in quad_tris:
                cand = texture.evaluate_sample(qt, i + 0.5, j + 0.5, None)
                if cand is not None:
                    ref = cand
                    break
            if ref is None:
                continue
            ref_err = max(
                ref_err,
                rel_err(ev_clip["u"], ref["u"]),
                rel_err(ev_clip["v"], ref["v"]),
                max(rel_err(ev_clip["gradient"][k], ref["gradient"][k])
                    for k in range(4)))
            ref_n += 1
    ok_ref = ref_n > 0 and ref_err <= 1e-9

    # A fully behind-near triangle must be entirely rejected.
    behind = [{"x": 0.0, "y": 0.0, "w": 0.2, "u": 0.0, "v": 0.0},
              {"x": 50.0, "y": 0.0, "w": 0.2, "u": 1.0, "v": 0.0},
              {"x": 25.0, "y": 50.0, "w": 0.2, "u": 0.5, "v": 1.0}]
    ok_behind = all(
        texture.evaluate_sample(behind, i + 0.5, j + 0.5, near) is None
        for j in range(0, 50, 4) for i in range(0, 50, 4))

    # degenerate primitives must be rejected
    degen = [{"x": 0.0, "y": 0.0, "w": 1.0, "u": 0.0, "v": 0.0},
             {"x": 4.0, "y": 4.0, "w": 1.0, "u": 1.0, "v": 1.0},
             {"x": 8.0, "y": 8.0, "w": 1.0, "u": 2.0, "v": 2.0}]
    ok_degen = texture.evaluate_sample(degen, 2.0, 2.0) is None
    data = {"texture": checker_texture(TEX_N),
            "samples": [{"x": 2.0, "y": 2.0, "vertices": degen}]}
    ok_null = texture.render(data)["results"][0]["color"] is None

    check("D. near-clip cuts new edge, keeps depth>=near, degenerate rejected",
          ok_clip and ok_ref and ok_behind and ok_degen and ok_null,
          "raw_px=%d clipped_px=%d depth_violations=%d min_depth=%.4f "
          "clip_ref_err=%.2e ref_pts=%d behind=%s degen=%s null=%s"
          % (kept, cut, violations, min_depth, ref_err, ref_n,
             ok_behind, ok_degen, ok_null))


def test_wrap_boundary():
    # identical footprints shifted by an integer number of UV tiles
    levels = texture.build_mip_chain(checker_texture(TEX_N))
    grad = [0.05, 0.002, -0.003, 0.04]
    stats1, stats2 = texture.SamplerStats(), texture.SamplerStats()
    max_diff = 0.0
    for shift in range(-3, 4):
        c1, m1, t1 = texture.sample_texture(levels, 0.997, 0.4, grad, stats1)
        c2, m2, t2 = texture.sample_texture(
            levels, 0.997 + shift, 0.4 + shift, grad, stats2)
        max_diff = max(max_diff, abs(c1 - c2), abs(m1 - m2),
                       abs(t1 - t2))
    # also a long-axis footprint that physically crosses u = integer boundary
    grad_cross = [0.22, 0.01, 0.0, 0.02]
    c_a, _, _ = texture.sample_texture(
        texture.build_mip_chain(checker_texture(TEX_N)),
        0.995, 0.5, grad_cross, texture.SamplerStats())
    c_b, _, _ = texture.sample_texture(
        texture.build_mip_chain(checker_texture(TEX_N)),
        1.995, 0.5, grad_cross, texture.SamplerStats())
    max_diff = max(max_diff, abs(c_a - c_b))
    check("E. periodic wrap invariance (incl. boundary-crossing footprint)",
          max_diff <= 1e-12,
          "max_shift_diff=%.3e" % max_diff)


def test_fallbacks():
    levels = texture.build_mip_chain(checker_texture(TEX_N))
    # magnification: sub-texel footprint
    st = texture.SamplerStats()
    c, mip, taps = texture.sample_texture(
        levels, 0.3, 0.3, [1e-4, 0.0, 0.0, 1e-4], st)
    ok_mag = st.mag_fallback == 1 and mip == 0.0 and taps == 1
    # extreme minification: footprint larger than the whole texture
    st2 = texture.SamplerStats()
    big = 4.0
    c2, mip2, taps2 = texture.sample_texture(
        levels, 0.5, 0.5, [big, 0.0, 0.0, big], st2)
    ok_min = st2.min_fallback == 1 and mip2 == len(levels) - 1 and taps2 == 1
    # non-finite / collapsed footprint
    st3 = texture.SamplerStats()
    c3, mip3, taps3 = texture.sample_texture(
        levels, 0.5, 0.5, [0.0, 0.0, 0.0, 0.0], st3)
    ok_pt = st3.point_fallback == 1 and taps3 == 1
    check("G. explicit magnification/minification/collapsed fallbacks",
          ok_mag and ok_min and ok_pt,
          "mag=%s min=%s(mip %.1f) collapsed=%s"
          % (ok_mag, ok_min, mip2, ok_pt))


def test_camera_paths():
    # path 1: lateral dolly with a near-plane crossing frame
    path1 = []
    for k in range(6):
        cam = Camera(eye=(-1.5 + 0.5 * k, 0.4 + 0.05 * k, 0.3),
                     target=(0.0, 0.0, -3.0), near=0.4, w=140, h=140)
        path1.append(cam)
    # path 2: inward push + elevation change, grazing frames
    path2 = []
    for k in range(6):
        elev = math.radians(8.0 + 4.0 * k)
        cam = Camera(eye=(0.3 * math.sin(k), 3.2 * math.tan(elev),
                          -0.5 - 0.35 * k),
                     target=(0.0, 0.0, -3.0), near=0.3, w=140, h=140)
        path2.append(cam)

    summary = []
    levels = texture.build_mip_chain(checker_texture(TEX_N))
    for pid, path in enumerate((path1, path2), 1):
        plane = make_plane(rot_z=math.radians(20.0 + 10 * pid), tiles=3.0)
        worst_diag = 0.0
        near_exercised = False
        wrap_exercised = False
        for fid, cam in enumerate(path):
            d = test_diagonal_swap(cam, plane,
                                   "path%d frame%d" % (pid, fid),
                                   near=cam.near)
            worst_diag = max(worst_diag, d)

            # Near clip on a screen triangle that straddles the near plane in
            # every frame (one vertex with depth below the near threshold).
            clip_tri = [
                {"x": 5.0, "y": 5.0, "w": 4.0, "u": 0.0, "v": 0.0},
                {"x": cam.w - 5.0, "y": 5.0, "w": 4.0, "u": 1.0, "v": 0.0},
                {"x": cam.w / 2.0, "y": cam.h - 5.0,
                 "w": 0.4 * cam.near, "u": 0.5, "v": 1.0}]
            n_raw = n_clip = viol = 0
            for j in range(5, cam.h - 5, 2):
                for i in range(5, cam.w - 5, 2):
                    if texture.evaluate_sample(clip_tri, i + 0.5,
                                               j + 0.5, None) is not None:
                        n_raw += 1
                    ev = texture.evaluate_sample(clip_tri, i + 0.5,
                                                 j + 0.5, cam.near)
                    if ev is not None:
                        n_clip += 1
                        if 1.0 / ev["inv_w"] < cam.near - 1e-9:
                            viol += 1
            if n_clip < n_raw and viol == 0 and n_clip > 0:
                near_exercised = True

            # wrap boundary: integer-shift invariance on this frame's
            # gradients, including footprints crossing integer UV boundaries
            grid = grid_mesh(plane, 2, 2)
            pgrid = projected_mesh(cam, grid)
            tris = [tri_diag_a(pgrid, x, y) for y in range(2)
                    for x in range(2)]
            for j in range(10, cam.h, 29):
                for i in range(10, cam.w, 29):
                    r = rasterize_color(levels, tris, i, j, near=cam.near)
                    if r is None:
                        continue
                    ev = r[3]
                    ca, _, _ = texture.sample_texture(
                        levels, ev["u"], ev["v"], ev["gradient"],
                        texture.SamplerStats())
                    cb, _, _ = texture.sample_texture(
                        levels, ev["u"] + 5.0, ev["v"] - 3.0,
                        ev["gradient"], texture.SamplerStats())
                    if abs(ca - cb) <= 1e-12:
                        wrap_exercised = True
        summary.append((pid, round(worst_diag, 6), near_exercised,
                        wrap_exercised))
    ok = all(d <= 0.01 and nc and wc for _, d, nc, wc in summary)
    check("F. two camera paths: diag-swap + near clip + wrap re-run",
          ok,
          "per_path (path,worst_dC,near_clip,wrap)=%s" % summary)


def main():
    print("== acceptance suite ==")
    test_analytic_derivatives()
    test_grazing_roi()
    cam = Camera(eye=(0.4, 0.9, 0.3), target=(0.0, 0.0, -3.0), w=160, h=160)
    plane = make_plane(rot_z=math.radians(22.0), tiles=3.0)
    test_diagonal_swap(cam, plane, "static oblique plane")
    test_near_clip_and_degenerate()
    test_wrap_boundary()
    test_fallbacks()
    test_camera_paths()
    failed = [n for n, ok, _ in RESULTS if not ok]
    print("\n%d/%d checks passed" % (len(RESULTS) - len(failed), len(RESULTS)))
    if failed:
        print("FAILED:", failed)
        raise SystemExit(1)


if __name__ == "__main__":
    main()