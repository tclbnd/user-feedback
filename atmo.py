#!/usr/bin/env python3
"""Precomputed atmospheric scattering lookup tables.

Two 2-D tables are built on fixed uniform grids (``lut_size`` x ``lut_size``):

* ``T[i][j]`` : transmittance along a view ray with zenith cosine
  ``mu = 2*i/(n-1) - 1`` from observer altitude ``h = 60*j/(n-1)`` km out to
  the top of the atmosphere (or to the ground, for downward rays that hit it).
* ``S[i][j]`` : single in-scattered radiance along the same view ray, with the
  sun at a fixed zenith cosine (``sun_cz``, default 0.7) and zero relative
  azimuth, so the scattering-angle cosine is determined by the grid ``mu``.

Physical model
--------------
Spherical Earth radius R_E = 6360 km, atmosphere top TOP = 60 km, density
``rho(h) = exp(-h/H)`` with scale height H = 8 km, Rayleigh extinction
beta = 5.5e-3 / km and phase function ``P(mu) = 3/(16*pi) * (1 + mu**2)``.

Near-horizon refraction is approximated by the standard "effective Earth
radius" trick: every ray-sphere intersection (path length, tangent/occlusion
tests) is computed with an Earth radius inflated by ``refraction`` (1.15).
Density is still evaluated with the true geometric altitude of each sample
point, which is the consistent formulation of the approximation.

Optical depth along a path is integrated per segment with the analytic
antiderivative of the exponential profile assuming linear altitude across the
segment (exact for straight rays in the flat limit, second-order accurate on
the curved geometry), plus a midpoint fallback.  This gives high accuracy with
a small fixed sample count and makes identical samples regardless of the
construction order.

Multi-scattering is folded into the single-scattering table through a constant
``MULTI_SCATTER = 1.05`` compensation multiplier applied to the integral.
"""
import hashlib
import json
import math
import sys
from concurrent.futures import ThreadPoolExecutor

# ---------------------------------------------------------------------------
# Model constants
# ---------------------------------------------------------------------------
R_E = 6360.0          # Earth radius [km]
H = 8.0               # density scale height [km]
BETA = 5.5e-3         # Rayleigh extinction coefficient [1/km]
TOP = 60.0            # atmosphere top altitude [km]
REFRACTION = 1.15     # effective Earth-radius factor (horizon refraction)
MULTI_SCATTER = 1.05  # multi-scattering compensation on single-scattering LUT


# ---------------------------------------------------------------------------
# Ray geometry (all radii use the refraction-inflated Earth radius)
# ---------------------------------------------------------------------------
def _r_e():
    """Effective Earth radius used for ray-sphere geometry."""
    return R_E * REFRACTION


def _observer_radius(alt):
    return _r_e() + alt


def path_length_to_top(r0, mu):
    """Distance along the ray (origin radius r0, zenith cosine mu) to TOP."""
    r_top = _r_e() + TOP
    b = r0 * mu
    disc = b * b + r_top * r_top - r0 * r0
    # Geometrically disc >= 0 whenever r0 <= r_top; clamp for FP safety.
    return -b + math.sqrt(max(0.0, disc))


def ground_hit_distance(r0, mu):
    """Distance to the ground sphere, or +inf if the ray never intersects."""
    if mu >= 0.0:
        return math.inf
    b = r0 * mu
    disc = b * b + _r_e() * _r_e() - r0 * r0
    if disc < 0.0:
        return math.inf
    return -b - math.sqrt(disc)


def radius_at(r0, mu, s):
    """Distance from Earth's centre at path distance s."""
    return math.sqrt(max(0.0, r0 * r0 + 2.0 * r0 * mu * s + s * s))


def altitude_at(r0, mu, s):
    """True geometric altitude [km] at path distance s."""
    return radius_at(r0, mu, s) - _r_e()


# ---------------------------------------------------------------------------
# Density profile and optical depth
# ---------------------------------------------------------------------------
def density(h):
    """Density at geometric altitude h [km]."""
    if h < 0.0:
        return 1.0
    if h > TOP:
        return 0.0
    return math.exp(-h / H)


def _segment_optical_depth(h0, h1, ds):
    """beta * integral of exp(-h(s)/H) ds over one segment.

    Altitude is assumed linear between h0 and h1, which makes the exponential
    integral analytic.  The ``expm1`` form avoids catastrophic cancellation
    when h0 and h1 are close (short segments / tangent rays); a midpoint rule
    guards the exactly-degenerate case.
    """
    if h0 >= TOP and h1 >= TOP:
        return 0.0
    dh = h1 - h0
    if abs(dh) > 1e-12:
        # beta * ds * rho(h0) * (1 - exp(-dh/H)) / (dh/H)
        z = -dh / H
        return BETA * ds * density(h0) * math.expm1(z) / z
    return BETA * density(0.5 * (h0 + h1)) * ds


def optical_depth(r0, mu, samples, density_fn=None):
    """Optical depth along the ray up to TOP (or the ground if it hits it).

    ``density_fn(h)`` overrides the default exponential profile, which is used
    by the analytic constant-density validation tests.
    """
    if density_fn is None:
        density_fn = density
    l_top = path_length_to_top(r0, mu)
    l_hit = ground_hit_distance(r0, mu)
    length = min(l_top, l_hit)
    if length <= 0.0:
        return 0.0
    ds = length / samples
    if density_fn is density:
        tau = 0.0
        s = 0.0
        h_prev = altitude_at(r0, mu, 0.0)
        for _ in range(samples):
            s += ds
            h_next = altitude_at(r0, mu, s)
            tau += _segment_optical_depth(h_prev, h_next, ds)
            h_prev = h_next
        return tau
    # Generic profile: midpoint quadrature (single beta multiplication).
    tau = 0.0
    for k in range(samples):
        s = (k + 0.5) * ds
        tau += density_fn(altitude_at(r0, mu, s)) * ds
    return BETA * tau


def transmittance(mu, alt, samples, density_fn=None):
    """Beam transmittance exp(-tau) along the view ray."""
    r0 = _observer_radius(alt)
    tau = optical_depth(r0, mu, samples, density_fn)
    return math.exp(-min(tau, 700.0))


# ---------------------------------------------------------------------------
# Single in-scattering
# ---------------------------------------------------------------------------
def _rayleigh_phase(mu):
    return 3.0 / (16.0 * math.pi) * (1.0 + mu * mu)


def sun_transmittance(r0_sun, mu_sun, samples):
    """Transmittance from a point to the sun; 0 inside the Earth shadow."""
    if mu_sun <= 0.0 and ground_hit_distance(r0_sun, mu_sun) < math.inf:
        return 0.0
    return math.exp(-min(optical_depth(r0_sun, mu_sun, samples), 700.0))


def _scatter_integrand(r0, mu, s, sin_theta, sun_sin, sun_cz,
                       tau_view, sun_samples):
    """One integrand sample: T_view * T_sun * P * beta * rho at distance s."""
    h = altitude_at(r0, mu, s)
    r_s = radius_at(r0, mu, s)
    # Local sun zenith cosine: x(s) = (s sin theta, 0, r0 + s mu) and the sun
    # direction is (sun_sin, 0, sun_cz).
    mu_sun_local = ((r0 + s * mu) * sun_cz
                    + s * sin_theta * sun_sin) / r_s
    t_sun = sun_transmittance(r_s, mu_sun_local, sun_samples)
    if t_sun <= 0.0:
        return 0.0
    t_view = math.exp(-min(tau_view, 700.0))
    return t_view * t_sun * BETA * density(h)


def inscatter(mu, alt, samples, sun_cz=0.7):
    """Single in-scattered radiance along a view ray (zero sun azimuth).

    Integral over path distance s of
        T_view(0->s) * T_sun(s) * P(mu_scatter) * beta * rho(h(s)) ds
    evaluated with a composite Simpson rule (even sample count), the
    Earth-shadow test applied to the sun ray at every node, and the constant
    ``MULTI_SCATTER`` compensation applied to the whole integral.  View optical
    depth is accumulated with the same analytic per-segment formula as the
    transmittance table, including at the Simpson midpoint nodes.  Each inner
    sun-ray optical depth uses a fixed half-resolution sample count, so the
    total cost stays O(samples^2) with a small, deterministic constant.
    """
    r0 = _observer_radius(alt)
    length = min(path_length_to_top(r0, mu), ground_hit_distance(r0, mu))
    if length <= 0.0:
        return 0.0
    n = max(2, samples - (samples % 2))  # Simpson needs an even count
    sun_samples = max(8, n // 2)
    ds = length / n

    # Local frame: view d = (sin theta, 0, cos theta); the sun shares the
    # azimuth (phi = 0) at elevation theta_s with cosine sun_cz.
    sin_theta = math.sqrt(max(0.0, 1.0 - mu * mu))
    sun_sin = math.sqrt(max(0.0, 1.0 - sun_cz * sun_cz))
    # Scattering-angle cosine between view and sun directions.
    mu_scatter = mu * sun_cz + sin_theta * sun_sin
    phase = _rayleigh_phase(mu_scatter)

    # Per-segment view optical depth, then cumulative tau at every node so the
    # integrand can be sampled at both nodes and segment midpoints.
    seg_tau = [0.0] * n
    h_prev = altitude_at(r0, mu, 0.0)
    for k in range(n):
        h_next = altitude_at(r0, mu, (k + 1) * ds)
        seg_tau[k] = _segment_optical_depth(h_prev, h_next, ds)
        h_prev = h_next

    def node_value(s, tau_view):
        return phase * _scatter_integrand(
            r0, mu, s, sin_theta, sun_sin, sun_cz, tau_view, sun_samples
        )

    total = node_value(0.0, 0.0) + node_value(length, sum(seg_tau))
    tau_cum = 0.0
    for k in range(n):
        mid_value = node_value((k + 0.5) * ds, tau_cum + 0.5 * seg_tau[k])
        end_value = node_value((k + 1) * ds, tau_cum + seg_tau[k])
        # Simpson weights: 4 on the midpoint, 2 on interior end nodes.
        total += 4.0 * mid_value + (2.0 if k < n - 1 else 0.0) * end_value
        tau_cum += seg_tau[k]
    return MULTI_SCATTER * total * ds / 6.0


# ---------------------------------------------------------------------------
# Table construction (order independent)
# ---------------------------------------------------------------------------
def _grid(n):
    return [2.0 * i / (n - 1) - 1.0 for i in range(n)], [
        TOP * j / (n - 1) for j in range(n)
    ]


def _build_cells(n, order):
    """All (i, j) cell indices in the requested construction order."""
    cells = [(i, j) for i in range(n) for j in range(n)]
    if order == "reverse":
        cells.reverse()
    elif order == "chunks":
        # Arbitrary block permutation: process 4x4 blocks in reversed order
        # and flip indices inside each block.
        block = 4
        reordered = []
        for bi in range(n - 1, -1, -block):
            for bj in range(n - 1, -1, -block):
                for i in range(bi, max(-1, bi - block), -1):
                    for j in range(bj, max(-1, bj - block), -1):
                        if 0 <= i < n and 0 <= j < n:
                            reordered.append((i, j))
        cells = reordered
    return cells


def build(cfg, order="sequential", threads=1):
    """Build both LUTs.

    The same (lut_size, samples, seed, sun_cz) always yields byte-identical
    tables: cells are pure functions of their indices, results are written into
    fixed-index slots, and only the evaluation order changes with order /
    threads.
    """
    n = int(cfg["lut_size"])
    s = int(cfg["samples"])
    sun_cz = float(cfg.get("sun_cz", 0.7))
    mus, alts = _grid(n)
    cells = _build_cells(n, order)

    T = [[0.0] * n for _ in range(n)]
    S = [[0.0] * n for _ in range(n)]

    def work(cell):
        i, j = cell
        mu, alt = mus[i], alts[j]
        return i, j, transmittance(mu, alt, s), inscatter(mu, alt, s, sun_cz)

    if threads and threads > 1:
        with ThreadPoolExecutor(max_workers=threads) as pool:
            results = list(pool.map(work, cells))
    else:
        results = [work(c) for c in cells]

    for i, j, t_val, s_val in results:
        T[i][j] = round(t_val, 9)
        S[i][j] = round(s_val, 9)

    payload = {"T": T, "S": S}
    checksum = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()[:16]
    return {
        "T": T,
        "S": S,
        "meta": {
            "lut_size": n,
            "samples": s,
            "sun_cz": sun_cz,
            "refraction": REFRACTION,
            "checksum": checksum,
        },
    }


# ---------------------------------------------------------------------------
# Clamped bilinear queries (continuous inside and across table boundaries)
# ---------------------------------------------------------------------------
def _bilerp(table, n, fx, fy):
    """Bilinear interpolation with fx, fy already clamped to [0, n-1]."""
    i0 = int(math.floor(fx))
    j0 = int(math.floor(fy))
    i1 = min(i0 + 1, n - 1)
    j1 = min(j0 + 1, n - 1)
    tx = fx - i0
    ty = fy - j0
    return (
        table[i0][j0] * (1.0 - tx) * (1.0 - ty)
        + table[i1][j0] * tx * (1.0 - ty)
        + table[i0][j1] * (1.0 - tx) * ty
        + table[i1][j1] * tx * ty
    )


def query(lut, name, x, y):
    """Look up a table.

    T is indexed by (cos_zenith, altitude); S by (scattering cosine mu,
    altitude).  Coordinates outside the grid are clamped to the border, so the
    returned function is continuous across every table edge.
    """
    table = lut[name]
    n = lut["meta"]["lut_size"]
    fx = (x + 1.0) * 0.5 * (n - 1)
    fy = y / TOP * (n - 1)
    fx = min(max(fx, 0.0), n - 1)
    fy = min(max(fy, 0.0), n - 1)
    return _bilerp(table, n, fx, fy)


def _main(argv):
    import argparse

    parser = argparse.ArgumentParser(
        description="Precomputed atmosphere transmittance/in-scatter LUTs")
    parser.add_argument("--query", nargs=4,
                        metavar=("LUT", "TABLE", "X", "Y"),
                        help="query mode, e.g. --query lut.json T cz alt")
    parser.add_argument("config", nargs="?", help="build config JSON")
    parser.add_argument("output", nargs="?", help="output LUT JSON")
    parser.add_argument("--order",
                        choices=("sequential", "reverse", "chunks"),
                        default="sequential",
                        help="cell construction order (must not change data)")
    parser.add_argument("--threads", type=int, default=1,
                        help="worker threads (must not change data)")
    args = parser.parse_args(argv[1:])

    if args.query:
        lut_path, name, x, y = args.query
        with open(lut_path) as f:
            lut = json.load(f)
        print(query(lut, name, float(x), float(y)))
        return 0

    if not args.config or not args.output:
        parser.error("config and output paths are required in build mode")
    with open(args.config) as f:
        cfg = json.load(f)
    data = build(cfg, order=args.order, threads=args.threads)
    with open(args.output, "w") as f:
        json.dump(data, f)
    print(data["meta"]["checksum"])
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv))
