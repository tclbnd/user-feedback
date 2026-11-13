"""Projective-texture replay with perspective-correct EWA anisotropic filtering.

Pipeline per sample:
  1. Rasterize the screen-space triangle (edge functions / barycentrics).
  2. Near clip in the 1/w (camera-space depth) plane. Vertices crossing the
     near plane are split with *affine* interpolation of (U/w, V/w, 1/w), so
     new edges introduced by clipping keep exact perspective-correct values.
  3. Evaluate continuous (never wrapped) UV and its screen-space gradients via
     the quotient rule on affinely-interpolated (U/w, V/w, 1/w).
  4. Build a mip chain by box pre-filtering in the linear luminance domain.
  5. EWA footprint: eigen-decomposition of the gradient Gram matrix gives the
     long/short axes; the short axis selects the mip level, the axis ratio
     controls the number of taps along the long axis. Repeat wrapping handles
     periodic texture boundaries without perturbing the derivatives.

Degenerate primitives and samples rejected by the near clip return
color=null, gradient=null. reference_mode="point" disables mip/anisotropy and
does a single level-0 bilinear tap (the acceptance baseline).
"""
import argparse
import json
import math
from pathlib import Path

EPS = 1e-12
MAX_ANISO = 16          # hard cap on long-axis taps
TRILINEAR_BLEND = True  # smooth mip transitions (kills inter-level flicker)


# --------------------------------------------------------------------------
# Mip chain: linear-domain 2x2 box pre-filter
# --------------------------------------------------------------------------
def build_mip_chain(base):
    """Square power-of-two luminance texture -> levels [N, N/2, ..., 1]."""
    levels = [base]
    cur = base
    while len(cur) > 1:
        h = len(cur)
        half = h // 2
        nxt = [[0.0] * half for _ in range(half)]
        for j in range(half):
            r0, r1 = cur[2 * j], cur[2 * j + 1]
            out = nxt[j]
            for i in range(half):
                c = 2 * i
                out[i] = 0.25 * (r0[c] + r0[c + 1] + r1[c] + r1[c + 1])
        levels.append(nxt)
        cur = nxt
    return levels


# --------------------------------------------------------------------------
# Bilinear sampling on one mip level with repeat (periodic) wrapping
# --------------------------------------------------------------------------
def bilinear_level(level, u, v):
    n = len(level)
    # texel centers at (i + 0.5) / n ; wrapping happens in texel space
    tx = u * n - 0.5
    ty = v * n - 0.5
    i0 = math.floor(tx)
    j0 = math.floor(ty)
    fx = tx - i0
    fy = ty - j0
    i0 %= n
    j0 %= n
    i1 = (i0 + 1) % n
    j1 = (j0 + 1) % n
    w00 = (1.0 - fx) * (1.0 - fy)
    w10 = fx * (1.0 - fy)
    w01 = (1.0 - fx) * fy
    w11 = fx * fy
    return (w00 * level[j0][i0] + w10 * level[j0][i1]
            + w01 * level[j1][i0] + w11 * level[j1][i1])


# --------------------------------------------------------------------------
# Triangle rasterization + near clip + perspective-correct gradients
# --------------------------------------------------------------------------
def _edge(ax, ay, bx, by, px, py):
    return (px - ax) * (by - ay) - (py - ay) * (bx - ax)


def evaluate_sample(vertices, px, py, near=None):
    """Return dict(u, v, inv_w, gradient) for a covered sample, else None.

    vertices: screen triangle, each {x, y, w, u, v}. Gradients use the
    continuous UVs (no modulo). Near clipping works in the 1/w plane.
    """
    a, b, c = vertices
    area = _edge(a["x"], a["y"], b["x"], b["y"], c["x"], c["y"])
    if abs(area) <= EPS:
        return None  # degenerate primitive

    wa = _edge(b["x"], b["y"], c["x"], c["y"], px, py)
    wb = _edge(c["x"], c["y"], a["x"], a["y"], px, py)
    wc = _edge(a["x"], a["y"], b["x"], b["y"], px, py)
    # consistent half-plane membership, supporting both triangle windings
    if area > 0:
        if min(wa, wb, wc) < -EPS:
            return None
    else:
        if max(wa, wb, wc) > EPS:
            return None
    bary = (wa / area, wb / area, wc / area)
    if any(lam < -EPS for lam in bary):
        return None

    # Per-vertex perspective-correct affine quantities.
    inv = [1.0 / q["w"] for q in vertices]
    uw = [q["u"] / q["w"] for q in vertices]
    vw = [q["v"] / q["w"] for q in vertices]

    # Near clip in the 1/w plane. 1/w grows as camera-space depth shrinks;
    # reject 1/w > 1/near, splitting edges that cross the threshold.
    if near is not None and near > 0.0:
        limit = 1.0 / near
        # Sutherland-Hodgman against the half-plane 1/w <= limit. Each carried
        # vertex stores screen (x, y) plus the affine quantities
        # (1/w, U/w, V/w), interpolated linearly on the original triangle.
        poly = []
        for k in range(3):
            bary_k = tuple(1.0 if r == k else 0.0 for r in range(3))
            sx = sum(bary_k[r] * vertices[r]["x"] for r in range(3))
            sy = sum(bary_k[r] * vertices[r]["y"] for r in range(3))
            poly.append((inv[k], sx, sy, bary_k))
        clipped = []
        for k in range(len(poly)):
            d_v, d_x, d_y, d_b = poly[k]
            s_v, s_x, s_y, s_b = poly[k - 1]
            d_in = d_v <= limit
            s_in = s_v <= limit

            def make_vertex(alpha):
                bm = tuple(s_b[r] + alpha * (d_b[r] - s_b[r])
                           for r in range(3))
                return (limit,
                        s_x + alpha * (d_x - s_x),
                        s_y + alpha * (d_y - s_y), bm)

            if d_in:
                if not s_in:
                    alpha = (limit - s_v) / (d_v - s_v)
                    clipped.append(make_vertex(alpha))
                clipped.append((d_v, d_x, d_y, d_b))
            elif s_in:
                alpha = (limit - s_v) / (d_v - s_v)
                clipped.append(make_vertex(alpha))
        if len(clipped) < 3:
            return None  # whole triangle behind the near plane
        # point-in-polygon on the clipped region (screen-space ray cast)
        inside = False
        for k in range(len(clipped)):
            _, x0, y0, _ = clipped[k - 1]
            _, x1, y1, _ = clipped[k]
            if ((y0 > py) != (y1 > py)) and \
               (px < x0 + (x1 - x0) * (py - y0) / (y1 - y0)):
                inside = not inside
        if not inside:
            return None
        # The sample's perspective-correct values and gradients are still
        # evaluated from the original triangle below: its affine quantities
        # (1/w, U/w, V/w) are screen-linear, so the point-in-polygon test only
        # gates coverage and the interpolation formula stays unchanged.
    # Affine screen-space gradients of the three interpolated quantities.
    # For affine g with vertex values g0,g1,g2, solve
    # [dx1 dy1; dx2 dy2] . grad(g) = [g1-g0; g2-g0] (screen y points down).
    dx1 = b["x"] - a["x"]
    dy1 = b["y"] - a["y"]
    dx2 = c["x"] - a["x"]
    dy2 = c["y"] - a["y"]
    det = dx1 * dy2 - dy1 * dx2
    if abs(det) <= EPS:
        return None

    def affine_grad(g):
        dg1 = g[1] - g[0]
        dg2 = g[2] - g[0]
        gx = (dg1 * dy2 - dg2 * dy1) / det
        gy = (dx1 * dg2 - dx2 * dg1) / det
        return gx, gy

    qx, qy = affine_grad(inv)      # grad of 1/w
    ax_, ay_ = affine_grad(uw)     # grad of U/w
    bx_, by_ = affine_grad(vw)     # grad of V/w

    q0 = sum(bary[t] * inv[t] for t in range(3))
    a0 = sum(bary[t] * uw[t] for t in range(3))
    b0 = sum(bary[t] * vw[t] for t in range(3))
    u = a0 / q0
    v = b0 / q0

    # Quotient rule: d/dx (A/q) = (dA/dx*q - A*dq/dx) / q^2
    q2 = q0 * q0
    du_dx = (ax_ * q0 - a0 * qx) / q2
    du_dy = (ay_ * q0 - a0 * qy) / q2
    dv_dx = (bx_ * q0 - b0 * qx) / q2
    dv_dy = (by_ * q0 - b0 * qy) / q2

    return {"u": u, "v": v, "inv_w": q0,
            "gradient": [du_dx, du_dy, dv_dx, dv_dy]}


# --------------------------------------------------------------------------
# EWA anisotropic sampler
# --------------------------------------------------------------------------
def footprint_axes(grad):
    """Long/short half-axis lengths (UV units) and major-axis direction."""
    du_dx, du_dy, dv_dx, dv_dy = grad
    # Gram matrix of the pixel -> UV map; eigenvectors give the footprint axes.
    g00 = du_dx * du_dx + dv_dx * dv_dx
    g01 = du_dx * du_dy + dv_dx * dv_dy
    g11 = du_dy * du_dy + dv_dy * dv_dy
    tr = g00 + g11
    disc = max(0.0, 0.25 * tr * tr - (g00 * g11 - g01 * g01))
    root = math.sqrt(disc)
    lam_hi = 0.5 * tr + root
    lam_lo = max(0.0, 0.5 * tr - root)
    major = math.sqrt(max(0.0, lam_hi))
    minor = math.sqrt(max(0.0, lam_lo))
    # eigenvector of lambda_hi (major axis), in UV space
    if abs(g01) > EPS:
        vec = (lam_hi - g11, g01)
        norm = math.hypot(*vec)
        axis = (vec[0] / norm, vec[1] / norm) if norm > 0.0 else (1.0, 0.0)
    else:
        axis = (1.0, 0.0) if g00 >= g11 else (0.0, 1.0)
    return major, minor, axis


class SamplerStats:
    def __init__(self):
        self.fragments = 0
        self.point_fallback = 0
        self.mag_fallback = 0
        self.min_fallback = 0
        self.tap_total = 0
        self.tap_max = 0
        self.mip_levels = []
        self.aniso_ratios = []
        self.major_texels = []
        self.minor_texels = []

    def summary(self):
        n = max(1, self.fragments)
        return {
            "fragments": self.fragments,
            "point_footprint_fallback": self.point_fallback,
            "magnification_fallback": self.mag_fallback,
            "minification_clamp_fallback": self.min_fallback,
            "mean_taps": self.tap_total / n,
            "max_taps": self.tap_max,
            "mip_min": (min(self.mip_levels) if self.mip_levels else 0.0),
            "mip_max": (max(self.mip_levels) if self.mip_levels else 0.0),
            "aniso_max": (max(self.aniso_ratios) if self.aniso_ratios else 0.0),
            "major_texel_max": (max(self.major_texels) if self.major_texels else 0.0),
            "minor_texel_max": (max(self.minor_texels) if self.minor_texels else 0.0),
        }


def sample_texture(levels, u, v, grad, stats, reference_point=False):
    """Return (color, mip_level, taps). reference_point => level-0 bilinear."""
    n = len(levels[0])
    top = len(levels) - 1

    if reference_point:
        return bilinear_level(levels[0], u, v), 0.0, 1

    major, minor, axis = footprint_axes(grad)
    major_t = 2.0 * major * n   # full-axis texel spans
    minor_t = 2.0 * minor * n
    stats.fragments += 1
    stats.major_texels.append(major_t)
    stats.minor_texels.append(minor_t)

    # Fallback 1: collapsed / non-finite footprint -> single level-0 tap.
    if not (math.isfinite(major_t) and math.isfinite(minor_t)) or major_t <= EPS:
        stats.point_fallback += 1
        stats.mip_levels.append(0.0)
        stats.aniso_ratios.append(1.0)
        stats.tap_total += 1
        stats.tap_max = max(stats.tap_max, 1)
        return bilinear_level(levels[0], u, v), 0.0, 1

    # Fallback 2: extreme magnification (footprint below one texel) -> level 0.
    if minor_t < 1.0 and major_t < 1.0:
        stats.mag_fallback += 1
        stats.mip_levels.append(0.0)
        stats.aniso_ratios.append(1.0)
        stats.tap_total += 1
        stats.tap_max = max(stats.tap_max, 1)
        return bilinear_level(levels[0], u, v), 0.0, 1

    # Mip level from the short axis so one bilinear tap spans ~1 texel.
    mip = max(0.0, math.log2(max(minor_t, 1e-12)))
    # Fallback 3: extreme minification -> clamp at the 1x1 level.
    if mip >= top:
        stats.min_fallback += 1
        stats.mip_levels.append(float(top))
        stats.aniso_ratios.append(1.0)
        stats.tap_total += 1
        stats.tap_max = max(stats.tap_max, 1)
        return levels[top][0][0], float(top), 1

    aniso = min(MAX_ANISO, max(1.0, major_t / max(minor_t, EPS)))
    taps = int(math.ceil(aniso))
    taps = max(1, min(MAX_ANISO, taps))
    stats.aniso_ratios.append(aniso)
    stats.mip_levels.append(mip)

    lo = int(math.floor(mip))
    hi = min(top, lo + 1)
    blend = mip - lo if TRILINEAR_BLEND else 0.0

    # Walk the long axis across the full footprint (half-span major in UV),
    # Gaussian-weighted; periodic wrapping is handled inside bilinear_level.
    axu, axv = axis
    if taps == 1:
        offset_list = [0.0]
        weights = [1.0]
    else:
        offset_list = []
        weights = []
        for k in range(taps):
            t = -1.0 + 2.0 * k / (taps - 1)       # -1 .. 1 along footprint
            offset_list.append(t * major)
            weights.append(math.exp(-2.0 * t * t))
    acc = 0.0
    wsum = 0.0
    for off, w in zip(offset_list, weights):
        uu = u + axu * off
        vv = v + axv * off
        c_lo = bilinear_level(levels[lo], uu, vv)
        if hi != lo and blend > 0.0:
            c_hi = bilinear_level(levels[hi], uu, vv)
            col = c_lo * (1.0 - blend) + c_hi * blend
        else:
            col = c_lo
        acc += w * col
        wsum += w

    stats.tap_total += taps
    stats.tap_max = max(stats.tap_max, taps)
    return acc / wsum, mip, taps


# --------------------------------------------------------------------------
# Driver
# --------------------------------------------------------------------------
def render(data):
    levels = build_mip_chain(data["texture"])
    near = data.get("near")
    point = data.get("reference_mode") == "point"
    stats = SamplerStats()
    results = []
    for s in data["samples"]:
        ev = evaluate_sample(s["vertices"], s["x"], s["y"], near)
        if ev is None:
            results.append({"color": None, "gradient": None,
                            "mip": None, "taps": None})
            continue
        color, mip, taps = sample_texture(
            levels, ev["u"], ev["v"], ev["gradient"], stats, point)
        results.append({
            "color": color,
            "gradient": ev["gradient"],
            "mip": mip,
            "taps": taps,
            "u": ev["u"],
            "v": ev["v"],
        })
    return {"results": results, "stats": stats.summary()}


def main():
    p = argparse.ArgumentParser()
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    a = p.parse_args()
    out = render(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(out, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
