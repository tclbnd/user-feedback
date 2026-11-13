#!/usr/bin/env python3
"""Precomputed atmosphere scattering LUTs.

Spherical-geometry model (all lengths in km):
  R_E   = 6360       earth radius
  R_EF  = 1.15*R_E   effective radius (refraction near horizon approximated
                     by straight rays around an enlarged planet)
  H     = 8          density scale height, rho(h) = exp(-h/H)
  BETA  = 5.5e-3     Rayleigh scattering coefficient /km
  TOP   = 60         top of atmosphere

Tables (i indexes the first coordinate, j the second):
  T[i][j]: transmittance T=exp(-tau) from altitude `alt` along a ray with
           zenith cosine `cz` to the top of the atmosphere. Rays whose
           perigee dips below R_EF hit the ground -> T = 0.
  S[i][j]: single inscatter luminance at altitude `alt` for sun zenith
           cosine CZ_SUN=0.7 and mu = cos(view, sun). The view ray lies in
           the sun/vertical plane with theta_view = theta_sun + arccos(mu):
              L = MS * P(mu) * BETA * ∫ T(P->Q) T(Q->sun) rho(h_Q) ds
           integrated along the view ray up to the top of atmosphere or
           the ground. P(mu) = 3/(16pi)(1+mu^2) (Rayleigh phase).
           T(Q->sun) = 0 when the segment Q->sun intersects the earth
           (shadow test). MS = 1.05 approximates multiple scattering.

All quadrature uses a fixed-count midpoint rule, so results are
deterministic for a given (lut_size, samples); each texel is a pure
function of its indices, hence build chunking/order cannot change data.

CLI:
  python3 atmo.py config.json output.json     build LUTs
  python3 atmo.py --query lut.json T cz alt   query transmittance
  python3 atmo.py --query lut.json S mu h     query inscatter
config.json: {"lut_size":16,"samples":32,"seed":1,"order":"forward"}
  `seed`  is accepted for compatibility (sampling is deterministic).
  `order` is one of forward|reverse|strided (texel evaluation order;
          the resulting tables are identical for any order).
"""
import json, sys, math, hashlib

R_E = 6360.0
R_EF = 1.15 * R_E
H = 8.0
BETA = 5.5e-3
TOP = 60.0
CZ_SUN = 0.7
MS_FACTOR = 1.05


def density(h):
    return math.exp(-max(0.0, h) / H)


def _far_hit(r0, cz, radius):
    """Distance along ray (r0, zenith cosine cz) to far sphere intersection."""
    disc = r0 * r0 * cz * cz - (r0 * r0 - radius * radius)
    return -r0 * cz + math.sqrt(max(0.0, disc))


def _hits_ground(r0, cz):
    """True if the ray (r0, cz) intersects the (effective) planet."""
    return cz < 0.0 and r0 * r0 * (1.0 - cz * cz) < R_EF * R_EF


def optical_depth(cz, alt, samples):
    """tau = BETA * ∫ rho ds along the ray to the top of the atmosphere."""
    r0 = R_EF + alt
    if _hits_ground(r0, cz):
        return math.inf
    length = _far_hit(r0, cz, R_EF + TOP)
    ds = length / samples
    tau = 0.0
    for k in range(samples):
        s = (k + 0.5) * ds
        r = math.sqrt(r0 * r0 + s * s + 2.0 * r0 * cz * s)
        tau += density(r - R_EF)
    return tau * ds * BETA


def transmittance(cz, alt, samples):
    tau = optical_depth(cz, alt, samples)
    if tau >= 700.0:
        return 0.0
    return math.exp(-tau)


def phase_rayleigh(mu):
    return 3.0 / (16.0 * math.pi) * (1.0 + mu * mu)


def _view_dir(mu):
    """View direction coplanar with sun and local vertical.

    Returns (sin_theta_v, cos_theta_v) with theta_v = theta_sun + arccos(mu).
    """
    s_s = math.sqrt(1.0 - CZ_SUN * CZ_SUN)
    s_m = math.sqrt(max(0.0, 1.0 - mu * mu))
    cz_v = CZ_SUN * mu - s_s * s_m
    sn_v = s_s * mu + CZ_SUN * s_m
    return sn_v, cz_v


def inscatter(mu, alt, samples, sun_samples=None):
    """Single inscatter luminance (with multiple-scatter compensation)."""
    if sun_samples is None:
        sun_samples = samples
    sn_v, cz_v = _view_dir(mu)
    s_s = math.sqrt(1.0 - CZ_SUN * CZ_SUN)
    r0 = R_EF + alt
    # path end: top of atmosphere, or ground if the view ray hits it
    length = _far_hit(r0, cz_v, R_EF + TOP)
    if _hits_ground(r0, cz_v):
        disc = r0 * r0 * cz_v * cz_v - (r0 * r0 - R_EF * R_EF)
        length = min(length, -r0 * cz_v - math.sqrt(max(0.0, disc)))
    if length <= 0.0:
        return 0.0
    ds = length / samples
    tau_prev = 0.0          # view-path optical depth (unscaled by BETA)
    acc = 0.0
    for k in range(samples):
        s = (k + 0.5) * ds
        qx = s * sn_v
        qz = r0 + s * cz_v
        rq = math.sqrt(qx * qx + qz * qz)
        h = rq - R_EF
        rho = density(h)
        t_view = math.exp(-min(700.0, BETA * (tau_prev + 0.5 * rho * ds)))
        # sun transmittance from Q, with earth-shadow test
        qs = (qx * s_s + qz * CZ_SUN) / rq   # local sun zenith cosine at Q
        if qs < 0.0 and rq * rq * (1.0 - qs * qs) < R_EF * R_EF:
            t_sun = 0.0                       # Q in earth shadow
        else:
            t_sun = transmittance(qs, h, sun_samples)
        acc += t_view * t_sun * rho
        tau_prev += rho * ds
    return MS_FACTOR * phase_rayleigh(mu) * BETA * acc * ds


def _texel_order(n, order):
    idx = [(i, j) for i in range(n) for j in range(n)]
    if order == "reverse":
        idx.reverse()
    elif order == "strided":
        idx = idx[::3] + idx[1::3] + idx[2::3]
    elif order != "forward":
        raise ValueError("unknown order: %r" % order)
    return idx


def build(cfg):
    n, s = cfg["lut_size"], cfg["samples"]
    order = cfg.get("order", "forward")
    idx = _texel_order(n, order)
    T = [[0.0] * n for _ in range(n)]
    S = [[0.0] * n for _ in range(n)]
    for i, j in idx:  # each texel is a pure function of (i, j): order-free
        T[i][j] = round(transmittance(2.0 * i / (n - 1) - 1.0,
                                      TOP * j / (n - 1), s), 9)
    for i, j in idx:
        S[i][j] = round(inscatter(2.0 * i / (n - 1) - 1.0,
                                  TOP * j / (n - 1), s), 9)
    h = hashlib.sha256(repr([T, S]).encode()).hexdigest()[:16]
    return {"T": T, "S": S,
            "meta": {"lut_size": n, "samples": s, "checksum": h}}


def query(lut, name, cz, alt):
    """Bilinear lookup; coordinates outside the table are clamped."""
    tab = lut[name]
    n = lut["meta"]["lut_size"]
    fx = min(max((cz + 1.0) * 0.5 * (n - 1), 0.0), n - 1.0)
    fy = min(max(alt / TOP * (n - 1), 0.0), n - 1.0)
    i0 = min(int(fx), n - 2)
    j0 = min(int(fy), n - 2)
    tx, ty = fx - i0, fy - j0
    return (tab[i0][j0] * (1 - tx) * (1 - ty) +
            tab[i0 + 1][j0] * tx * (1 - ty) +
            tab[i0][j0 + 1] * (1 - tx) * ty +
            tab[i0 + 1][j0 + 1] * tx * ty)


if __name__ == "__main__":
    if sys.argv[1] == "--query":
        lut = json.load(open(sys.argv[2]))
        print(query(lut, sys.argv[3], float(sys.argv[4]), float(sys.argv[5])))
    else:
        cfg = json.load(open(sys.argv[1]))
        json.dump(build(cfg), open(sys.argv[2], "w"))
