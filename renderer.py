"""Spectral path replay renderer with one shared estimator weight.

Three sampling decisions are placed under a single joint proposal density:

    p_joint = p_wavelength * p_light * p_transport * p_scatter

* wavelength proposal  : uniform over the wavelength grid by default, or an
                         explicit per-wavelength proposal table;
* light proposal       : the area-light ``pdf`` fields (must sum to 1);
* transport proposal   : deterministic distance replay (p_t = 1, Beer law
                         carried in the numerator) or free-flight distance
                         sampling inside participating media;
* scatter proposal     : only used when a material declares ``scatter``;
                         absorption/scattering are competing events sharing
                         one normalised proposal distribution.

Every Monte Carlo sample divides one numerator (true spectral contribution)
by that joint pdf, so light, scatter and transport weights live in the same
measurement framework.  ``spp = 0`` performs deterministic full enumeration
and is the zero-variance reference used for bias/noise reporting.

Scheduling is tile based but RNG draws are counter-based keyed on
(pixel, sample, channel, seed): swapping tile order or worker count changes
nothing in the per-pixel sequence, hence nothing in the output digest.
"""
from __future__ import annotations

import hashlib
import json
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import List, Optional, Sequence

from medium import trace_events
from spectralrng import CounterRNG, categorical_index
from spectra import (linear_srgb_to_display, resolve_basis,
                     xyz_to_linear_srgb)


# ---------------------------------------------------------------------------
# Online statistics (Welford) so energy bias and noise are reported separately
# ---------------------------------------------------------------------------
class Welford:
    """Mean / variance accumulator for one scalar channel."""

    __slots__ = ("n", "mean", "m2", "total")

    def __init__(self):
        self.n = 0
        self.mean = 0.0
        self.m2 = 0.0
        self.total = 0.0

    def add(self, x: float) -> None:
        self.n += 1
        self.total += x
        delta = x - self.mean
        self.mean += delta / self.n
        self.m2 += delta * (x - self.mean)

    @property
    def variance(self) -> float:
        return self.m2 / self.n if self.n > 1 else 0.0

    @property
    def std(self) -> float:
        return math.sqrt(self.variance)


# ---------------------------------------------------------------------------
# Scene normalisation
# ---------------------------------------------------------------------------
@dataclass
class LightBranch:
    spectrum: List[float]
    weight: float          # true integral-branch weight (need not sum to 1)
    pdf: float             # proposal probability (must sum to 1 over lights)


@dataclass
class Scene:
    wavelengths: List[float]
    basis: List[List[float]]
    spacing: List[float]
    materials: dict
    rays: List[dict]
    lights: List[LightBranch]
    seed: int
    spp: int
    transport: str
    wavelength_pdf: List[float]
    reference_white: List[float]
    exposure: float

    @property
    def n_wave(self) -> int:
        return len(self.wavelengths)


def load_scene(config: dict) -> Scene:
    wavelengths, basis, spacing = resolve_basis(config)
    n = len(wavelengths)

    materials = config["materials"]
    for name, m in materials.items():
        if len(m["ior"]) != n or len(m["absorb"]) != n:
            raise ValueError("material %s arrays must match wavelength count"
                             % name)
        if any(a < 0.0 for a in m["absorb"]):
            raise ValueError("material %s has negative absorption" % name)

    rays = config["rays"]
    # Lights may be global or per ray; per ray overrides global.
    global_lights = config.get("lights", [])
    lights = []
    for li, lc in enumerate(global_lights):
        if len(lc["spectrum"]) != n:
            raise ValueError("light %d spectrum length mismatch" % li)
        lights.append(LightBranch([float(v) for v in lc["spectrum"]],
                                  float(lc["weight"]), float(lc["pdf"])))
    if lights and abs(sum(l.pdf for l in lights) - 1.0) > 1e-9:
        raise ValueError("global light pdfs must sum to 1, got %r"
                         % [l.pdf for l in lights])

    wave_pdf_cfg = config.get("wavelength_pdf")
    if wave_pdf_cfg is None:
        wave_pdf = [1.0 / n] * n
    else:
        if len(wave_pdf_cfg) != n:
            raise ValueError("wavelength_pdf length mismatch")
        s = sum(wave_pdf_cfg)
        wave_pdf = [float(p) / s for p in wave_pdf_cfg]

    return Scene(
        wavelengths=wavelengths,
        basis=basis,
        spacing=spacing,
        materials=materials,
        rays=rays,
        lights=lights,
        seed=int(config.get("seed", 1)),
        spp=int(config.get("spp", 0)),
        transport=str(config.get("transport", "analytic")),
        wavelength_pdf=wave_pdf,
        reference_white=[float(v) for v in
                         config.get("reference_white", [1.0, 1.0, 1.0])],
        exposure=float(config.get("exposure", 1.0)),
    )


def _ray_lights(scene: Scene, ray: dict) -> List[LightBranch]:
    if ray.get("lights"):
        lights = []
        for lc in ray["lights"]:
            lights.append(LightBranch([float(v) for v in lc["spectrum"]],
                                      float(lc["weight"]),
                                      float(lc["pdf"])))
        if abs(sum(l.pdf for l in lights) - 1.0) > 1e-9:
            raise ValueError("ray light pdfs must sum to 1")
        return lights
    return scene.lights


def _true_light_spectrum(lights: Sequence[LightBranch], wi: int) -> float:
    """Spectral radiance from the true branch weights (not the proposal)."""
    return sum(l.weight * l.spectrum[wi] for l in lights)


# ---------------------------------------------------------------------------
# Transport sampling: analytic Beer law vs free-flight in a medium segment
# ---------------------------------------------------------------------------
def sample_segment(rng: CounterRNG, segment: dict, mode: str):
    """Return (value_estimate, pdf_transport, events) for one distance event.

    analytic   : deterministic replay.  Absorption is already carried by the
                 cached Beer-Lambert ``trace.transmission`` in the numerator,
                 so the transport value here is 1 with p_t = 1 (a delta
                 strategy) to avoid double counting the Beer factor.
    freeflight : sample an exponential free-flight distance with rate
                 sigma_t = sigma_a + sigma_s over the segment length L.
                 A flight beyond L means the segment is crossed: the
                 indicator is 1 with probability exp(-sigma_t L).  In this
                 mode the cached Beer term is suppressed (it equals the
                 free-flight survival expectation) and the transport value
                 carries the interaction outcome instead.
    """
    L = segment["length"]
    sigma_a = segment["sigma_a"]
    sigma_s = segment.get("scatter", 0.0)
    sigma_t = sigma_a + sigma_s
    if mode != "freeflight" or sigma_t <= 0.0 or L <= 0.0:
        return 1.0, 1.0, dict(mode=mode if mode == "freeflight"
                              else "analytic", scatter_hits=0)
    # Ratio tracking (Novak et al.).  Tentative collisions are proposed with
    # free-flight distance rate sigma_t; at each one the transmittance
    # weight is multiplied by the absorption fraction sigma_a/sigma_t.
    # No random event-type choice is needed for the weight: analytically,
    # E[product of fractions over the Poisson collision count] equals
    # exp(-sigma_a L), the Beer-Lambert transmittance.  A separate scatter
    # channel draw is kept only for scatter-hit diagnostics, so the scatter
    # proposal still occupies its named RNG channel and stays reproducible.
    weight = 1.0
    x = 0.0
    scatter_hits = 0
    n_interactions = 0
    while True:
        u = rng.draw_exp(CounterRNG.CH_TRANSPORT, sigma_t)
        if x + u >= L:
            # p_t = 1 keeps the joint proposal denominator well-defined;
            # the stochastic transmittance lives entirely in ``weight``.
            return weight, 1.0, dict(mode="freeflight",
                                     scatter_hits=scatter_hits,
                                     interactions=n_interactions,
                                     crossed=True)
        x += u
        n_interactions += 1
        if sigma_s <= 0.0:
            # Purely absorbing medium (delta tracking): the first real
            # collision is an absorption and terminates the path, so the
            # survival probability is exp(-sigma_t L) = Beer-Lambert here.
            return 0.0, 1.0, dict(mode="freeflight",
                                  scatter_hits=scatter_hits,
                                  interactions=n_interactions,
                                  absorbed=True)
        # Participating medium (ratio tracking).  Every tentative collision
        # multiplies the weight by the non-absorbed fraction
        # 1 - sigma_a/sigma_t.  With N ~ Poisson(sigma_t L) collisions:
        #   E[w] = sum_n P(N=n) (1 - sa/st)^n
        #        = exp(-st L * sa/st) = exp(-sa L),
        # exactly Beer-Lambert.  The scatter channel draw is diagnostic.
        weight *= 1.0 - sigma_a / sigma_t
        rng.draw(CounterRNG.CH_SCATTER)
        scatter_hits += 1


# ---------------------------------------------------------------------------
# Per-wavelength deterministic trace cache and per-sample evaluation
# ---------------------------------------------------------------------------
@dataclass
class RayEval:
    traces: List[object]
    lights: List[LightBranch]


def _precompute(scene: Scene, ray: dict) -> RayEval:
    traces = [trace_events(ray, scene.materials, wi)
              for wi in range(scene.n_wave)]
    for wi, tr in enumerate(traces):
        if not tr.ok:
            raise ValueError("ray trace failed at wavelength %d: %s"
                             % (wi, tr.error))
    return RayEval(traces=traces, lights=_ray_lights(scene, ray))


def _transport_value_pdf(rng: CounterRNG, trace, mode: str):
    value = 1.0
    pdf = 1.0
    scatter_hits = 0
    for seg in trace.segments:
        v, p, info = sample_segment(rng, seg, mode)
        value *= v
        pdf *= p
        scatter_hits += info.get("scatter_hits", 0)
    return value, pdf, scatter_hits


def evaluate_sample(scene: Scene, pre: RayEval, pixel: int,
                    sample_index: int) -> dict:
    """One joint sample: wavelength x light branch x transport strategy."""
    rng = CounterRNG(pixel, sample_index, scene.seed)
    wi = categorical_index(rng, CounterRNG.CH_WAVELENGTH,
                           scene.wavelength_pdf)
    li = categorical_index(rng, CounterRNG.CH_LIGHT,
                           [l.pdf for l in pre.lights])
    trace = pre.traces[wi]
    light = pre.lights[li]

    transport_value, p_transport, scatter_hits = _transport_value_pdf(
        rng, trace, scene.transport)

    # In analytic mode the cached Beer-Lambert transmission carries
    # absorption.  In free-flight mode the transport estimator already
    # accounts for survival/absorption, so the cached term is suppressed to
    # avoid multiplying it twice.
    absorption_term = (1.0 if scene.transport == "freeflight"
                       else trace.transmission)

    # Numerator: true spectral contribution for the chosen light branch,
    # including its real (un-normalised) branch weight.  Grid spacing
    # weights carry the integration measure (mean-normalised, so uniform
    # grids reduce to the original sum/n convention).
    numerator = (light.weight * light.spectrum[wi]
                 * absorption_term * transport_value
                 * scene.spacing[wi])
    # Joint proposal pdf shared by light / transport / scatter decisions.
    p_joint = scene.wavelength_pdf[wi] * light.pdf * p_transport
    # The 1/n factor is the grid normalisation matching the enumeration
    # integral sum_k spacing_k f_k / n; without it the MC estimate carries
    # a spurious factor of n.
    weight = (numerator / p_joint / scene.n_wave
              if p_joint > 0.0 else 0.0)

    basis = scene.basis[wi]
    xyz = [weight * b for b in basis]
    return dict(wi=wi, li=li, xyz=xyz, weight=weight,
                p_joint=p_joint, scatter_hits=scatter_hits,
                exit_angle=trace.exit_angle,
                transmission=absorption_term * transport_value,
                tir=trace.tir)


def enumerate_reference(scene: Scene, pre: RayEval) -> dict:
    """Deterministic full enumeration (spp=0): zero-variance reference."""
    n = scene.n_wave
    xyz = [0.0, 0.0, 0.0]
    per_wave = []
    for wi in range(n):
        trace = pre.traces[wi]
        radiance = _true_light_spectrum(pre.lights, wi)
        value = radiance * trace.transmission * scene.spacing[wi]
        per_wave.append(value)
        for c in range(3):
            xyz[c] += value * scene.basis[wi][c] / n
    return dict(xyz=xyz, per_wave=per_wave)


# ---------------------------------------------------------------------------
# Pixel rendering with separated bias / noise statistics
# ---------------------------------------------------------------------------
def render_pixel(scene: Scene, pixel: int, pre: RayEval,
                 reference: Optional[dict] = None) -> dict:
    if scene.spp == 0:
        ref = enumerate_reference(scene, pre)
        return dict(xyz=ref["xyz"], per_wave=ref["per_wave"],
                    samples=0, variance=[0.0, 0.0, 0.0],
                    sample_energy=[], scatter_hits=0,
                    transmission=[pre.traces[wi].transmission
                                  for wi in range(scene.n_wave)],
                    exit_angles=[pre.traces[wi].exit_angle
                                 for wi in range(scene.n_wave)],
                    tir=[pre.traces[wi].tir
                         for wi in range(scene.n_wave)],
                    fresnel=[pre.traces[wi].fresnel
                             for wi in range(scene.n_wave)],
                    stack=[pre.traces[wi].stack
                           for wi in range(scene.n_wave)])

    acc = [Welford(), Welford(), Welford()]
    energies = []
    scatter_total = 0
    chosen = []
    for s in range(scene.spp):
        ev = evaluate_sample(scene, pre, pixel, s)
        for c in range(3):
            acc[c].add(ev["xyz"][c])
        energies.append(sum(ev["xyz"]))
        scatter_total += ev["scatter_hits"]
        chosen.append(dict(wi=ev["wi"], li=ev["li"],
                           weight=ev["weight"], p_joint=ev["p_joint"]))
    xyz = [a.mean for a in acc]
    variance = [a.variance for a in acc]
    return dict(xyz=xyz, variance=variance, samples=scene.spp,
                sample_energy=energies, scatter_hits=scatter_total,
                chosen=chosen,
                transmission=[pre.traces[wi].transmission
                              for wi in range(scene.n_wave)],
                exit_angles=[pre.traces[wi].exit_angle
                             for wi in range(scene.n_wave)],
                tir=[pre.traces[wi].tir
                     for wi in range(scene.n_wave)],
                fresnel=[pre.traces[wi].fresnel
                         for wi in range(scene.n_wave)],
                stack=[pre.traces[wi].stack
                       for wi in range(scene.n_wave)])


# ---------------------------------------------------------------------------
# Tiled scheduling
# ---------------------------------------------------------------------------
def _tiles(count: int, tile: int) -> List[List[int]]:
    return [list(range(i, min(i + tile, count)))
            for i in range(0, count, tile)]


def render(config: dict, tile: int = 4, workers: int = 1,
           tile_order: Optional[Sequence[int]] = None) -> dict:
    scene = load_scene(config)
    n_pixels = len(scene.rays)
    pres = [_precompute(scene, ray) for ray in scene.rays]
    references = [enumerate_reference(scene, pre) for pre in pres]

    tile_groups = _tiles(n_pixels, max(1, tile))
    if tile_order is None:
        order = list(range(len(tile_groups)))
    else:
        order = list(tile_order)
        if sorted(order) != list(range(len(tile_groups))):
            raise ValueError("tile_order must be a permutation of tile ids")

    results: List[Optional[dict]] = [None] * n_pixels

    def work(indices: Sequence[int]):
        # The worker receives only pixel indices; every draw is a pure
        # function of (pixel, sample, seed), so execution order cannot leak
        # into the samples.
        return [(i, render_pixel(scene, i, pres[i], references[i]))
                for i in indices]

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(work, tile_groups[g]) for g in order]
            for fut in futures:
                for i, res in fut.result():
                    results[i] = res
    else:
        for g in order:
            for i, res in work(tile_groups[g]):
                results[i] = res

    return _assemble(scene, results, references, pres)


def _assemble(scene: Scene, results: Sequence[dict],
              references: Sequence[dict],
              pres: Sequence[RayEval]) -> dict:
    image_xyz = [r["xyz"] for r in results]

    # Single, explicit linear pipeline: XYZ -> linear sRGB -> encoded sRGB.
    linear_srgb = [xyz_to_linear_srgb(v, scene.reference_white)
                   for v in image_xyz]
    display_srgb = [linear_srgb_to_display(v, exposure=scene.exposure)
                    for v in linear_srgb]

    rays_out = []
    bias_components = []
    noise_components = []
    energy_bias = []
    for i, r in enumerate(results):
        ref = references[i]["xyz"]
        est = r["xyz"]
        bias = [est[c] - ref[c] for c in range(3)]
        # Monte Carlo standard error of the mean per channel.
        sem = [math.sqrt(r["variance"][c] / r["samples"])
               if r["samples"] > 0 else 0.0 for c in range(3)]
        bias_components.extend(bias)
        noise_components.extend(sem)
        energy_bias.append(sum(est) - sum(ref))
        traces = pres[i].traces
        rays_out.append(dict(
            transmission=r["transmission"],
            exit_angles=r["exit_angles"],
            tir=r["tir"],
            tir_events=[t.tir_events for t in traces],
            fresnel=r["fresnel"],
            stack=r["stack"],
            xyz=est,
            reference_xyz=ref,
            reference_per_wave=references[i]["per_wave"],
            linear_srgb=linear_srgb[i],
            display_srgb=display_srgb[i],
            variance=r["variance"],
            standard_error=sem,
            bias=bias,
            samples=r["samples"],
            scatter_hits=r["scatter_hits"],
            sample_energy=r["sample_energy"],
            chosen=r.get("chosen", []),
        ))

    mean_abs_bias = (sum(abs(b) for b in bias_components)
                     / max(1, len(bias_components)))
    rms_noise = math.sqrt(sum(x * x for x in noise_components)
                          / max(1, len(noise_components)))
    mean_energy_bias = (sum(energy_bias) / len(energy_bias)
                        if energy_bias else 0.0)
    digest = hashlib.sha256(
        json.dumps(image_xyz, sort_keys=True).encode("utf-8")).hexdigest()

    return dict(
        rays=rays_out,
        image=image_xyz,
        image_linear_srgb=linear_srgb,
        image_display_srgb=display_srgb,
        stats=dict(
            pixels=len(results),
            spp=scene.spp,
            transport=scene.transport,
            seed=scene.seed,
            mean_abs_xyz_bias=mean_abs_bias,
            rms_standard_error=rms_noise,
            mean_energy_bias=mean_energy_bias,
            total_abs_energy_bias=sum(abs(e) for e in energy_bias),
            reference_energy=[sum(r["xyz"]) for r in references],
            estimated_energy=[sum(v) for v in image_xyz],
            wavelength_pdf=scene.wavelength_pdf,
        ),
        digest=digest,
        wavelengths=scene.wavelengths,
    )
