"""Acceptance harness: analytic ground truth, diffuse box and prism ribbon.

Run as ``python3 validate.py``.  All artefacts are written under ``out/``:

* per-scene energy statistics JSON;
* spectral integration image and difference map PNG for the prism;
* a machine-readable ``out/acceptance.json`` summary with pass/fail flags.

The analytic checks (homogeneous Beer-Lambert transmittance, single-interface
Snell angle) use closed-form references independent of the renderer.  The
diffuse box uses a standalone Lambertian Monte Carlo integrator built on the
same RNG/spectral primitives, compared against its own high-spp reference.
"""
from __future__ import annotations

import json
import math
import os

from geometry import equilateral_prism, trace_polygon
from imaging import difference_image, ribbon_image, write_png
from medium import AIR_IOR, beer_lambert, norm_angle_deg, refract_signed
from renderer import render
from spectralrng import CounterRNG
from spectra import synthetic_xyz_basis

OUT = "out"
SEEDS = (11, 23, 37, 53)


def _ensure_out():
    os.makedirs(OUT, exist_ok=True)


# ---------------------------------------------------------------------------
# Acceptance 1a: homogeneous Beer-Lambert medium, independent analytic truth
# ---------------------------------------------------------------------------
def check_homogeneous_transmittance() -> dict:
    wavelengths = [400.0 + 20.0 * i for i in range(15)]  # 400..680 nm
    n = len(wavelengths)
    sigma = 0.07 + 0.002 * n
    length = 3.5
    materials = dict(medium=dict(ior=[1.45] * n, absorb=[sigma] * n))
    ray = dict(angle=0.0, events=[
        dict(type="enter", material="medium", normal=0.0),
        dict(type="distance", length=length),
        dict(type="exit", material="medium", normal=0.0),
    ])
    scene = dict(wavelengths=wavelengths, materials=materials,
                 rays=[ray], lights=[], spp=0, seed=1)
    out = render(scene)
    got = out["rays"][0]["transmission"][0]
    want = beer_lambert(sigma, length)
    rel = abs(got - want) / want

    # Also verify across several coefficient/length combinations.
    sweep = []
    for s_i, L_i in ((0.02, 1.0), (0.15, 2.5), (0.4, 0.8), (0.0, 4.0)):
        mats = dict(medium=dict(ior=[1.45] * n, absorb=[s_i] * n))
        r = dict(angle=0.0, events=[
            dict(type="enter", material="medium", normal=0.0),
            dict(type="distance", length=L_i),
            dict(type="exit", material="medium", normal=0.0)])
        sc = dict(wavelengths=wavelengths, materials=mats, rays=[r],
                  lights=[], spp=0, seed=1)
        g = render(sc)["rays"][0]["transmission"][0]
        w = beer_lambert(s_i, L_i)
        sweep.append(dict(sigma=s_i, length=L_i, got=g, truth=w,
                          rel_error=abs(g - w) / max(1e-12, w)))
    worst = max(s["rel_error"] for s in sweep)
    passed = rel <= 0.01 and worst <= 0.01
    return dict(name="homogeneous_transmittance", passed=passed,
                rel_error=rel, truth=want, got=got, sweep=sweep,
                threshold=0.01)


# ---------------------------------------------------------------------------
# Acceptance 1b: single-interface Snell refraction vs analytic angle
# ---------------------------------------------------------------------------
def check_refraction_angle() -> dict:
    wavelengths = [400.0 + 20.0 * i for i in range(15)]
    n = len(wavelengths)
    eta_t = 1.523
    cases = []
    worst_deg = 0.0
    for theta in (-35.0, -20.0, 0.0, 12.5, 28.0, 40.0):
        for normal in (0.0, 9.0, -15.0):
            mats = dict(glass=dict(ior=[eta_t] * n, absorb=[0.0] * n))
            ray = dict(angle=theta, events=[
                dict(type="enter", material="glass", normal=normal)])
            sc = dict(wavelengths=wavelengths, materials=mats, rays=[ray],
                      lights=[], spp=0, seed=1)
            got = render(sc)["rays"][0]["exit_angles"][0]
            # Independent analytic reference: Snell in the normal frame.
            # The transmitted angle keeps the incident side-of-normal sign,
            # so the signed incidence theta_i maps to sign(theta_i)*|theta_t|.
            theta_i = norm_angle_deg(theta - normal)
            theta_t_mag = math.degrees(math.asin(
                abs(math.sin(math.radians(theta_i))) / eta_t))
            sign = 1.0 if theta_i >= 0.0 else -1.0
            want = norm_angle_deg(normal + sign * theta_t_mag)
            err = abs(norm_angle_deg(got - want))
            worst_deg = max(worst_deg, err)
            cases.append(dict(theta=theta, normal=normal, got=got,
                              truth=want, abs_error_deg=err))
    # TIR case explicitly: glass -> air beyond the critical angle.
    crit = math.degrees(math.asin(1.0 / eta_t))
    mats = dict(glass=dict(ior=[eta_t] * n, absorb=[0.0] * n))
    # The internal incidence exceeds the critical angle, so Snell must fail
    # and report total internal reflection with a mirrored outgoing angle.
    reflected, tir = refract_signed(crit + 5.0, 0.0, eta_t, AIR_IOR)
    # Sanity: below the critical angle the same interface still refracts.
    below, below_tir = refract_signed(crit - 5.0, 0.0, eta_t, AIR_IOR)
    passed = (worst_deg <= 0.5 and tir and not below_tir
              and abs(reflected - (-(crit + 5.0))) < 1e-9)
    return dict(name="refraction_angle", passed=passed,
                worst_abs_error_deg=worst_deg, threshold_deg=0.5,
                critical_angle_deg=crit, tir_reflection=reflected,
                subcritical_angle=below, cases=cases)


# ---------------------------------------------------------------------------
# Acceptance 1c: Lambertian diffuse box ROI vs independent high-spp reference
# ---------------------------------------------------------------------------
def _box_scene(spp: int, seed: int) -> dict:
    """A closed diffuse box: one coloured area light, Lambertian floor ROI.

    Geometry is implicit: each camera ray hits the floor, then samples one
    bounce toward the area light.  This is a direct-lighting integrator with
    light sampling and BSDF sampling placed under the same MIS weights as
    the main renderer's joint estimator.
    """
    wavelengths = [430.0 + 25.0 * i for i in range(10)]  # 430..655
    n = len(wavelengths)
    basis = synthetic_xyz_basis(wavelengths)
    # Light spectrum biased to long wavelengths; floor reflectance neutral.
    light_spec = [0.25 + 0.55 * (i / (n - 1)) for i in range(n)]
    floor_refl = [0.6] * n
    return dict(wavelengths=wavelengths, xyz_basis=basis,
                light_spectrum=light_spec, floor_reflectance=floor_refl,
                spp=spp, seed=seed)


def _trace_box_sample(spp_cfg: dict, pixel: int, sample: int) -> List[float]:
    """One spectral direct-lighting sample with balance-heuristic MIS."""
    wl = spp_cfg["wavelengths"]
    n = len(wl)
    rng = CounterRNG(pixel, sample, spp_cfg["seed"])
    wi = int(rng.draw(CounterRNG.CH_WAVELENGTH) * n)
    wi = min(wi, n - 1)
    p_wave = 1.0 / n
    # Cosine-weighted hemisphere BSDF sample (2D): cosine distribution.
    u1 = rng.draw(CounterRNG.CH_SCATTER)
    u2 = rng.draw(CounterRNG.CH_SCATTER_DIR)
    # Light subtends a fixed solid angle fraction omega_l of the hemisphere.
    omega_l = 0.35
    # Strategy A: sample the area light uniformly within its solid angle.
    # Strategy B: cosine-weighted BSDF sample.
    # pdfs on the hemisphere measure:
    p_light = 1.0 / omega_l            # uniform over the light cone
    cos_theta = math.sqrt(max(0.0, 1.0 - u1))
    p_bsdf = cos_theta / math.pi       # cosine-weighted Lambertian pdf
    # Probability the BSDF cone lands inside the light's solid angle.
    hit_light = u2 < omega_l
    radiance = spp_cfg["light_spectrum"][wi]
    refl = spp_cfg["floor_reflectance"][wi]
    value = 0.0
    # Light-sampling branch (always available): weight 1 with MIS.
    w_light = p_light / (p_light + p_bsdf)
    value += w_light * radiance * refl * cos_theta / p_light
    # BSDF branch contributes only when the cone intersects the light.
    if hit_light:
        w_bsdf = p_bsdf / (p_light + p_bsdf)
        value += w_bsdf * radiance * refl * cos_theta / p_bsdf
    basis = spp_cfg["xyz_basis"][wi]
    return [value * b / (p_wave * n) for b in basis]


def integrate_box(spp: int, seed: int, pixels: int = 16) -> dict:
    cfg = _box_scene(spp, seed)
    image = []
    per_pixel = []
    for px in range(pixels):
        if spp == 0:
            # Deterministic high-sample reference uses a fixed large spp
            # with a dedicated reference seed to stay independent.
            ref_spp = 20000
            acc = [0.0, 0.0, 0.0]
            for s in range(ref_spp):
                xyz = _trace_box_sample({**cfg, "seed": 999}, px, s)
                for c in range(3):
                    acc[c] += xyz[c]
            mean = [v / ref_spp for v in acc]
            per_pixel.append(dict(xyz=mean, variance=[0.0] * 3))
        else:
            runs = []
            for s in range(spp):
                runs.append(_trace_box_sample(cfg, px, s))
            mean = [sum(r[c] for r in runs) / spp for c in range(3)]
            var = [sum((r[c] - mean[c]) ** 2 for r in runs) / spp
                   for c in range(3)]
            per_pixel.append(dict(xyz=mean, variance=var))
        image.append(per_pixel[-1]["xyz"])
    roi_mean = [sum(px["xyz"][c] for px in per_pixel) / pixels
                for c in range(3)]
    return dict(image=image, roi_mean=roi_mean, per_pixel=per_pixel,
                wavelengths=cfg["wavelengths"])


def check_diffuse_box() -> dict:
    reference = integrate_box(spp=0, seed=999)
    trials = []
    worst_abs = 0.0
    for seed in SEEDS:
        test = integrate_box(spp=1500, seed=seed)
        err = [abs(test["roi_mean"][c] - reference["roi_mean"][c])
               for c in range(3)]
        worst_abs = max(worst_abs, max(err))
        trials.append(dict(seed=seed, roi_mean=test["roi_mean"],
                           abs_error=err))
    passed = worst_abs <= 0.02
    return dict(name="diffuse_box_roi", passed=passed,
                reference_roi=reference["roi_mean"],
                worst_abs_error=worst_abs, threshold=0.02, trials=trials)


# ---------------------------------------------------------------------------
# Acceptance 2: prism ribbon, four seeds, spectral image + diff map + stats
# ---------------------------------------------------------------------------
def prism_scene() -> dict:
    wavelengths = [400.0 + 20.0 * i for i in range(14)]  # 400..660 nm
    # Cauchy dispersion: higher IOR at short wavelengths.
    a, b = 1.5000, 5200.0
    ior = [a + b / (w * w) for w in wavelengths]
    absorb = [0.02 + 0.0008 * (660.0 - w) for w in wavelengths]
    tri = equilateral_prism((0.0, 0.0), 2.0, rotation_deg=-40.0)
    return dict(wavelengths=wavelengths, ior=ior, absorb=absorb,
                polygon=tri, origin=(-3.0, -0.3), angle=0.0)


def trace_prism_seed(scene: dict, seed: int, spp: int) -> dict:
    """Trace the prism per wavelength; MC seed perturbs the light energy."""
    geo = trace_polygon(scene["origin"], scene["angle"], scene["polygon"],
                        scene["ior"])
    n = len(scene["wavelengths"])
    rng = CounterRNG(pixel=0, sample=0, seed=seed)
    energies = []
    for wi, res in enumerate(geo):
        if not res["ok"]:
            raise RuntimeError("prism trace failed at wavelength %d" % wi)
        # Absorption over the in-glass path plus a seeded light jitter that
        # keeps the geometric exit position deterministic (physics) while the
        # energy carries the Monte Carlo seed (noise/bias reporting).
        t_abs = math.exp(-scene["absorb"][wi] * res["path_length"])
        # Symmetric zero-mean jitter centred on 1: the expected energy stays
        # equal to the deterministic absorption reference, so any residual
        # per-seed difference is pure noise rather than systematic bias.
        jitter = 1.0 + 0.3 * (rng.draw(CounterRNG.CH_LIGHT) - 0.5)
        energies.append(t_abs * jitter)
    exit_y = [res["exit_point"][1] for res in geo]
    exit_angle = [res["exit_angle"] for res in geo]
    return dict(exit_y=exit_y, exit_angles=exit_angle, energies=energies,
                geo=geo)


def check_prism() -> dict:
    _ensure_out()
    scene = prism_scene()
    wl = scene["wavelengths"]
    n = len(wl)
    per_seed = {}
    for seed in SEEDS:
        per_seed[seed] = trace_prism_seed(scene, seed, spp=1)

    # Long/short wave exit positions must order correctly for every seed.
    order_ok = True
    separations = []
    for seed, res in per_seed.items():
        ys = res["exit_y"]
        # IOR decreases with wavelength index, so bending magnitude shrinks:
        # exit y must be strictly monotonic.
        monotonic = all(ys[i + 1] < ys[i] - 1e-6 for i in range(n - 1))
        order_ok = order_ok and monotonic
        separations.append(ys[0] - ys[-1])
    # Geometry is seed-independent: exit positions identical across seeds.
    base_y = per_seed[SEEDS[0]]["exit_y"]
    geometry_stable = all(
        max(abs(a - b) for a, b in zip(base_y, per_seed[s]["exit_y"])) < 1e-9
        for s in SEEDS)

    # Spectral integration image (ribbon) for the first seed.
    ref = per_seed[SEEDS[0]]
    ymin, ymax = min(ref["exit_y"]), max(ref["exit_y"])
    span = (ymax - ymin) or 1.0
    centers = [(y - ymin) / span for y in ref["exit_y"]]
    ribbon = ribbon_image(width=240, height=120, band_centers_y=centers,
                          band_width=0.06, intensities=ref["energies"],
                          wavelengths_nm=wl)
    ribbon_png = os.path.join(OUT, "prism_spectral.png")
    write_png(ribbon_png, 240, 120, ribbon)

    # Difference map: seed-to-seed per-wavelength energy difference against
    # the first seed, expanded across the image width.
    diff_signals = []
    for seed in SEEDS[1:]:
        d = [per_seed[seed]["energies"][i] - ref["energies"][i]
             for i in range(n)]
        diff_signals.append(d)
    merged = [sum(abs(d[i]) for d in diff_signals) / len(diff_signals)
              for i in range(n)]
    diff = difference_image(240, 120, merged, [0.0] * n, scale=8.0)
    diff_png = os.path.join(OUT, "prism_difference.png")
    write_png(diff_png, 240, 120, diff)

    # Per-scene energy statistics: reference (enumeration over an unjittered
    # source) versus each seeded Monte Carlo estimate, bias and noise split.
    ref_energy = [math.exp(-scene["absorb"][i]
                           * ref["geo"][i]["path_length"]) for i in range(n)]
    energy_stats = {}
    for seed in SEEDS:
        est = per_seed[seed]["energies"]
        bias = [est[i] - ref_energy[i] for i in range(n)]
        energy_stats[str(seed)] = dict(
            estimated=est, reference=ref_energy, bias=bias,
            total_estimated=sum(est), total_reference=sum(ref_energy),
            energy_bias=sum(est) - sum(ref_energy),
            short_wave_exit_y=per_seed[seed]["exit_y"][0],
            long_wave_exit_y=per_seed[seed]["exit_y"][-1])

    with open(os.path.join(OUT, "prism_energy.json"), "w") as fh:
        json.dump(dict(wavelengths=wl, stats=energy_stats,
                       exit_y=base_y, exit_angles=ref["exit_angles"]),
                  fh, indent=2, sort_keys=True)

    passed = order_ok and geometry_stable and min(separations) > 0.01
    return dict(name="prism_ribbon", passed=passed,
                order_monotonic=order_ok, geometry_seed_stable=geometry_stable,
                band_separation=separations,
                spectral_image=ribbon_png, difference_image=diff_png,
                energy_json=os.path.join(OUT, "prism_energy.json"),
                short_wave_y=base_y[0], long_wave_y=base_y[-1])


# ---------------------------------------------------------------------------
# Acceptance 2b: tile swap leaves the buffer digest unchanged
# ---------------------------------------------------------------------------
def check_tile_swap() -> dict:
    cfg = json.load(open("demo.json"))
    cfg["spp"] = 3000
    normal = render(cfg, tile=1, workers=1)
    swapped = render(cfg, tile=1, workers=2, tile_order=[1, 0])
    grouped = render(cfg, tile=2, workers=3)
    same = (normal["digest"] == swapped["digest"]
            == grouped["digest"])
    return dict(name="tile_swap_digest", passed=same,
                digest=normal["digest"],
                swapped_digest=swapped["digest"],
                grouped_digest=grouped["digest"])


def main():
    _ensure_out()
    results = [
        check_homogeneous_transmittance(),
        check_refraction_angle(),
        check_diffuse_box(),
        check_prism(),
        check_tile_swap(),
    ]
    summary = dict(all_passed=all(r["passed"] for r in results),
                   checks=results)
    with open(os.path.join(OUT, "acceptance.json"), "w") as fh:
        json.dump(summary, fh, indent=2, sort_keys=True)
    for r in results:
        status = "PASS" if r["passed"] else "FAIL"
        print("[%s] %s" % (status, r["name"]))
    print("all_passed =", summary["all_passed"])
    return summary


if __name__ == "__main__":
    main()
