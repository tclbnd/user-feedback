#!/usr/bin/env python3
"""Precomputed atmosphere LUTs (spherical geometry, refraction-aware, deterministic).

Usage:
  python3 atmo.py config.json output.json      # build T and S tables
  python3 atmo.py --query lut.json T cz alt    # transmittance query (clamped)
  python3 atmo.py --query lut.json S mu h      # in-scatter query (clamped)

Model
-----
Spherical earth R_E=6360 km, atmosphere top at TOP=60 km, exponential density
rho(h)=exp(-h/H), Rayleigh extinction beta (1/km).  All integration uses the
midpoint rule with a deterministic per-texel sample count
n = clamp(max(samples, ceil(L/STEP)), samples, N_MAX), so results depend only
on (lut_size, samples, model parameters) -- never on build order.

T table (transmittance)
-----------------------
T[i][j]: i -> cos_zenith, j -> altitude, stored value T = exp(-tau) where tau
is the optical depth from the point to the top of the atmosphere.
Coordinates use the Bruneton(2008) distance reparameterisation, which puts
extra resolution at the horizon and makes tau exactly linear in the table
coordinate for constant density:
    rho = sqrt(r^2 - R_E^2),  x_r = rho / H_MAP
    d   = dmin + x_mu*(dmax-dmin),  mu = (R_TOP^2 - r^2 - d^2)/(2 r d)
Near-horizon refraction is approximated with the effective-earth-radius
model: the ray is kept straight while R_E is scaled by k = 1+(refr_k-1)*w,
where w blends smoothly from 1 at the surface to 0 above refr_blend_km
(4/3-earth style correction; refr_k=1.15 default).  Queries below the horizon
or outside the table are clamped to the boundary texel; bilinear interpolation
is done in log(T) (i.e. optical-depth) space, which keeps the steep horizon
gradient well behaved and C0 continuous.

S table (single in-scatter)
---------------------------
S[i][j]: i -> mu = cos(view zenith) in [-1,1], j -> altitude in [0,TOP] km,
for a fixed sun (config sun_cz, default 0.7), view direction in the
sun-observer vertical plane.  Stored value is the path-integrated single
scattered radiance (sun irradiance = 1):
    S = ms_factor * integral of T(O->P) T(P->sun) P_Ray(cos_gamma) beta rho(h_P) ds
with the anisotropic Rayleigh phase P_Ray(g)=3/(16pi)(1+g^2).  Earth
occlusion: if the segment P->sun intersects the ground the contribution is
zero.  Multiple scattering is approximated by the deterministic scale factor
ms_factor (default 1.05).  The view ray terminates at the ground.

Determinism
-----------
Every texel is a pure function of its indices and the config; texels are
written into preallocated arrays, so chunk count / chunk order of the build
(build_tables(cfg, n_chunks, reverse)) does not change any texel.  Values are
rounded to 9 decimals; the checksum is sha256(repr([T,S]))[:16].
"""
import json, sys, math, hashlib

R_E, TOP = 6360.0, 60.0
R_TOP = R_E + TOP
H_MAP = math.sqrt(R_TOP * R_TOP - R_E * R_E)   # sqrt(R_TOP^2 - R_E^2), km
STEP = 0.5        # max ray-march step, km
N_MAX = 20000     # max march samples per path

DEFAULTS = {
    "lut_size": 16,
    "samples": 32,
    "seed": 1,               # accepted for compatibility; output never depends on it
    "beta": 5.5e-3,          # Rayleigh extinction, 1/km
    "scale_height": 8.0,     # H, km
    "sun_cz": 0.7,           # cos(sun zenith) for the S table
    "refr_k": 1.15,          # effective earth radius multiplier at the surface
    "refr_blend_km": 10.0,   # perigee height above which refraction fades out
    "ms_factor": 1.05,       # multiple-scattering approximation factor
}


def full_config(cfg):
    c = dict(DEFAULTS)
    c.update(cfg)
    return c


# ---------------------------------------------------------------- geometry
def _dist_top(r, mu, rtop):
    """Distance from radius r along cos-zenith mu to the top sphere."""
    disc = r * r * (mu * mu - 1.0) + rtop * rtop
    return -r * mu + math.sqrt(max(0.0, disc))


def _dist_ground(r, mu, rground):
    """Distance to the ground sphere, or -1 if the ray does not hit it."""
    if mu >= 0.0:
        return -1.0
    disc = r * r * (mu * mu - 1.0) + rground * rground
    if disc <= 0.0:
        return -1.0
    return -r * mu - math.sqrt(disc)      # >= 0


def _smooth01(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3.0 - 2.0 * x)


def _refr_k(mu, alt, cfg):
    """Effective-radius multiplier for a ray (mu, alt); 1 = geometric optics.

    The blend weight depends on the geometric (k=1) perigee altitude, so the
    correction is a deterministic function of the texel and vanishes for
    high-altitude / thin paths (where it would corrupt analytic agreement).
    """
    rk, blend = cfg["refr_k"], cfg["refr_blend_km"]
    if rk == 1.0 or blend <= 0.0:
        return 1.0
    if mu < 0.0:
        r = R_E + alt
        hp = r * math.sqrt(max(0.0, 1.0 - mu * mu)) - R_E
    else:
        hp = alt
    return 1.0 + (rk - 1.0) * _smooth01(1.0 - hp / blend)


# ---------------------------------------------------------------- quadrature
def optical_depth(mu, alt, n_min, cfg):
    """Midpoint-rule optical depth from (alt, cos-zenith mu) to the top of the
    atmosphere (or to the ground if the ray hits it).  Deterministic."""
    H, beta = cfg["scale_height"], cfg["beta"]
    k = _refr_k(mu, alt, cfg)
    Re, Rt = R_E * k, R_E * k + TOP
    r = Re + alt
    lt = _dist_top(r, mu, Rt)
    lg = _dist_ground(r, mu, Re)
    L = min(lt, lg) if lg >= 0.0 else lt
    if L <= 0.0:
        return 0.0
    n = max(int(n_min), int(math.ceil(L / STEP)))
    n = min(n, N_MAX)
    ds = L / n
    rr, rm = r * r, 2.0 * r * mu
    tau = 0.0
    for i in range(n):
        t = (i + 0.5) * ds
        d = math.sqrt(rr + t * t + rm * t)
        tau += math.exp(-max(0.0, d - Re) / H)
    return tau * ds * beta


# ---------------------------------------------------------------- T table
def _t_texel_coords(i, j, n):
    """Bruneton distance parameterisation (k=1 coordinate system)."""
    xr = j / (n - 1)
    rho = xr * H_MAP
    r = math.sqrt(R_E * R_E + rho * rho)
    xmu = i / (n - 1)
    dmin, dmax = R_TOP - r, rho + H_MAP
    d = dmin + xmu * (dmax - dmin)
    mu = 1.0 if d <= 0.0 else (R_TOP * R_TOP - r * r - d * d) / (2.0 * r * d)
    return mu, r - R_E


def _t_texel(i, j, n, cfg):
    mu, alt = _t_texel_coords(i, j, n)
    tau = optical_depth(mu, alt, cfg["samples"], cfg)
    return round(math.exp(-tau), 9)


def query_T(lut, cz, alt):
    """Clamped bilinear query of the T table, interpolated in log(T)
    (optical-depth) space.  Below-horizon cz clamps to the horizon texel."""
    tab, n = lut["T"], lut["meta"]["lut_size"]
    a = min(max(alt, 0.0), TOP)
    r = R_E + a
    rho = math.sqrt(max(0.0, r * r - R_E * R_E))
    xr = rho / H_MAP
    dmin, dmax = R_TOP - r, rho + H_MAP
    d = _dist_top(r, min(max(cz, -1.0), 1.0), R_TOP)
    xmu = (d - dmin) / (dmax - dmin)
    xmu = min(max(xmu, 0.0), 1.0)
    fy, fx = xr * (n - 1), xmu * (n - 1)
    j0 = min(int(fy), n - 2)
    i0 = min(int(fx), n - 2)
    ty, tx = fy - j0, fx - i0
    # log-space (optical depth) interpolation; T > 0 always
    l00 = math.log(tab[i0][j0])
    l10 = math.log(tab[i0 + 1][j0])
    l01 = math.log(tab[i0][j0 + 1])
    l11 = math.log(tab[i0 + 1][j0 + 1])
    lo = (l00 * (1 - tx) + l10 * tx) * (1 - ty) + (l01 * (1 - tx) + l11 * tx) * ty
    return math.exp(lo)


# ---------------------------------------------------------------- S table
def _rayleigh_phase(cg):
    return 3.0 / (16.0 * math.pi) * (1.0 + cg * cg)


def _s_texel(i, j, n, cfg, ttab):
    mu_v = 2.0 * i / (n - 1) - 1.0
    alt = TOP * j / (n - 1)
    mu_s = cfg["sun_cz"]
    H, beta = cfg["scale_height"], cfg["beta"]
    sv = math.sqrt(max(0.0, 1.0 - mu_v * mu_v))
    ss = math.sqrt(max(0.0, 1.0 - mu_s * mu_s))
    phase = _rayleigh_phase(mu_v * mu_s + sv * ss)   # anisotropic, principal plane
    k = _refr_k(mu_v, alt, cfg)
    Re, Rt = R_E * k, R_E * k + TOP
    r = Re + alt
    lt = _dist_top(r, mu_v, Rt)
    lg = _dist_ground(r, mu_v, Re)
    L = min(lt, lg) if lg >= 0.0 else lt
    if L <= 0.0:
        return 0.0
    m = max(int(cfg["samples"]), int(math.ceil(L / STEP)))
    m = min(m, N_MAX)
    ds = L / m
    tau_v, acc = 0.0, 0.0
    for s in range(m):
        t = (s + 0.5) * ds
        px, pz = t * sv, r + t * mu_v
        rp = math.sqrt(px * px + pz * pz)
        rho = math.exp(-max(0.0, rp - Re) / H)
        tv = math.exp(-(tau_v + 0.5 * beta * rho * ds))   # T(O->P), midpoint
        tau_v += beta * rho * ds
        mu_sp = (ss * px + mu_s * pz) / rp                # sun cos-zenith at P
        if _dist_ground(rp, mu_sp, R_E) < 0.0:            # not earth-occluded
            ts = query_T({"T": ttab, "meta": {"lut_size": n}}, mu_sp, rp - R_E)
        else:
            ts = 0.0
        acc += tv * ts * rho * ds
    return round(beta * phase * cfg["ms_factor"] * acc, 9)


def query_S(lut, mu, h):
    """Clamped bilinear query of the S table (linear space)."""
    tab, n = lut["S"], lut["meta"]["lut_size"]
    fx = (min(max(mu, -1.0), 1.0) + 1.0) * 0.5 * (n - 1)
    fy = min(max(h, 0.0), TOP) / TOP * (n - 1)
    i0 = min(int(fx), n - 2)
    j0 = min(int(fy), n - 2)
    tx, ty = fx - i0, fy - j0
    return ((tab[i0][j0] * (1 - tx) + tab[i0 + 1][j0] * tx) * (1 - ty)
            + (tab[i0][j0 + 1] * (1 - tx) + tab[i0 + 1][j0 + 1] * tx) * ty)


# ---------------------------------------------------------------- build
def _texel_order(total, n_chunks, reverse):
    """Yield texel indices chunk by chunk.  Order never affects the result:
    every texel is computed independently and written to its own slot."""
    n_chunks = max(1, int(n_chunks))
    size = int(math.ceil(total / n_chunks))
    chunks = [(c, min(c + size, total)) for c in range(0, total, size)]
    if reverse:
        chunks.reverse()
    for a, b in chunks:
        for idx in range(a, b):
            yield idx


def build_tables(cfg, n_chunks=1, reverse=False):
    cfg = full_config(cfg)
    n, samples = cfg["lut_size"], cfg["samples"]
    T = [[0.0] * n for _ in range(n)]
    for idx in _texel_order(n * n, n_chunks, reverse):
        i, j = divmod(idx, n)
        T[i][j] = _t_texel(i, j, n, cfg)
    S = [[0.0] * n for _ in range(n)]
    for idx in _texel_order(n * n, n_chunks, reverse):
        i, j = divmod(idx, n)
        S[i][j] = _s_texel(i, j, n, cfg, T)
    checksum = hashlib.sha256(repr([T, S]).encode()).hexdigest()[:16]
    return {"T": T, "S": S,
            "meta": {"lut_size": n, "samples": samples, "checksum": checksum}}


def query(lut, name, x, y):
    return query_T(lut, x, y) if name == "T" else query_S(lut, x, y)


if __name__ == "__main__":
    if sys.argv[1] == "--query":
        lut = json.load(open(sys.argv[2]))
        print(query(lut, sys.argv[3], float(sys.argv[4]), float(sys.argv[5])))
    else:
        cfg = json.load(open(sys.argv[1]))
        json.dump(build_tables(cfg), open(sys.argv[2], "w"))
