#!/usr/bin/env python3
"""Deterministic spectral path-event renderer.

Wavelength, light, scatter and transmission proposal densities are kept in
one Monte Carlo weight.  Geometry is supplied as ordered boundary events, so
this module is an offline replayer rather than a geometric intersection engine.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

AIR_NAME = "__air__"
EPS = 1.0e-12


class SpectralConfigError(ValueError):
    """Raised when a scene or probability table is invalid."""


def _normalize_angle_180(angle_deg: float) -> float:
    angle = float(angle_deg)
    while angle <= -180.0:
        angle += 360.0
    while angle > 180.0:
        angle -= 360.0
    return angle


def _as_float_list(values: Sequence[Any], name: str) -> List[float]:
    if not isinstance(values, list) or not values:
        raise SpectralConfigError(f"{name} must be a non-empty list")
    out: List[float] = []
    for value in values:
        if not isinstance(value, (int, float)) or not math.isfinite(value):
            raise SpectralConfigError(f"{name} contains a non-finite value")
        out.append(float(value))
    return out


def _validate_probabilities(values: Sequence[float], name: str) -> None:
    if any(value < 0.0 for value in values):
        raise SpectralConfigError(f"{name} must be non-negative")
    total = sum(values)
    if abs(total - 1.0) > 1.0e-10:
        raise SpectralConfigError(f"{name} must sum to 1, got {total!r}")


def validate_config(config: Dict[str, Any]) -> None:
    """Validate arrays, materials and normalized proposal densities."""
    wavelengths = config.get("wavelengths")
    if not isinstance(wavelengths, list) or not wavelengths:
        raise SpectralConfigError("wavelengths must be a non-empty list")
    m = len(wavelengths)

    basis = config.get("xyz_basis")
    if not isinstance(basis, list) or len(basis) != m:
        raise SpectralConfigError("xyz_basis length must match wavelengths")
    for row in basis:
        if not isinstance(row, list) or len(row) != 3:
            raise SpectralConfigError("each xyz_basis row must have 3 values")
        _as_float_list(row, "xyz_basis")

    pdf = _as_float_list(config.get("wavelength_pdf", [1.0 / m] * m),
                         "wavelength_pdf")
    if len(pdf) != m:
        raise SpectralConfigError("wavelength_pdf length mismatch")
    _validate_probabilities(pdf, "wavelength_pdf")

    weights = _as_float_list(config.get("wavelength_weights", [1.0 / m] * m),
                             "wavelength_weights")
    if len(weights) != m:
        raise SpectralConfigError("wavelength_weights length mismatch")
    if any(value < 0.0 for value in weights):
        raise SpectralConfigError("wavelength_weights must be non-negative")
    if abs(sum(weights) - 1.0) > 1.0e-10:
        raise SpectralConfigError("wavelength_weights must sum to 1")

    materials = config.get("materials", {})
    if not isinstance(materials, dict):
        raise SpectralConfigError("materials must be an object")
    for name, material in materials.items():
        if not isinstance(material, dict):
            raise SpectralConfigError(f"material {name} must be an object")
        ior = _as_float_list(material.get("ior"), f"materials.{name}.ior")
        absorb = _as_float_list(material.get("absorb"),
                                f"materials.{name}.absorb")
        if len(ior) != m or len(absorb) != m:
            raise SpectralConfigError(f"material {name} array length mismatch")
        if any(value <= 0.0 for value in ior):
            raise SpectralConfigError(f"material {name} ior must be positive")
        if any(value < 0.0 for value in absorb):
            raise SpectralConfigError(f"material {name} absorb must be >= 0")

    rays = config.get("rays")
    if not isinstance(rays, list) or not rays:
        raise SpectralConfigError("rays must be a non-empty list")

    for ray_index, ray in enumerate(rays):
        ray_name = ray.get("id", f"ray-{ray_index}")
        if not isinstance(ray.get("events", []), list):
            raise SpectralConfigError(f"{ray_name}: events must be a list")
        stack: List[str] = []
        for event_index, event in enumerate(ray.get("events", [])):
            etype = event.get("type")
            if etype in ("enter", "exit"):
                material = event.get("material")
                if material not in materials:
                    raise SpectralConfigError(
                        f"{ray_name}.events[{event_index}]: unknown material "
                        f"{material!r}"
                    )
                if etype == "enter":
                    stack.append(material)
                else:
                    if not stack:
                        raise SpectralConfigError(
                            f"{ray_name}: exit outside any medium"
                        )
                    expected = event.get("material", stack[-1])
                    if expected != stack[-1]:
                        raise SpectralConfigError(
                            f"{ray_name}: exit {expected!r} but stack top is "
                            f"{stack[-1]!r}"
                        )
                    stack.pop()
            elif etype == "distance":
                length = event.get("length")
                if not isinstance(length, (int, float)) or length < 0:
                    raise SpectralConfigError(
                        f"{ray_name}: distance length must be non-negative"
                    )
                if event.get("sample_transmission"):
                    mu = event.get("sample_rate")
                    if not isinstance(mu, (int, float)) or mu <= 0:
                        raise SpectralConfigError(
                            f"{ray_name}: sample_rate must be positive when "
                            "sample_transmission is enabled"
                        )
            elif etype in ("scatter", "path_end"):
                branches = event.get("branches")
                if branches is None:
                    branches = [event]
                branch_pdf = _as_float_list(
                    [branch.get("pdf", 1.0) for branch in branches],
                    f"{ray_name}.scatter.pdf",
                )
                _validate_probabilities(branch_pdf,
                                        f"{ray_name}.scatter.pdf")
                for branch in branches:
                    weight = branch.get("weight", 1.0)
                    if not isinstance(weight, (int, float)) or weight < 0:
                        raise SpectralConfigError(
                            f"{ray_name}: scatter weight must be non-negative"
                        )
            else:
                raise SpectralConfigError(
                    f"{ray_name}: unsupported event type {etype!r}"
                )

        lights = ray.get("lights", [])
        if not lights:
            lights = [{"spectrum": [1.0] * m, "weight": 1.0, "pdf": 1.0}]
        light_pdf = _as_float_list(
            [light.get("pdf", 1.0) for light in lights],
            f"{ray_name}.lights.pdf",
        )
        _validate_probabilities(light_pdf, f"{ray_name}.lights.pdf")
        for light_index, light in enumerate(lights):
            spectrum = _as_float_list(
                light.get("spectrum"),
                f"{ray_name}.lights[{light_index}].spectrum",
            )
            if len(spectrum) != m:
                raise SpectralConfigError(
                    f"{ray_name}.lights[{light_index}]: spectrum length mismatch"
                )
            weight = light.get("weight", 1.0)
            if not isinstance(weight, (int, float)) or weight < 0:
                raise SpectralConfigError(f"{ray_name}: light weight < 0")

    width = config.get("width")
    height = config.get("height")
    if width is not None and height is not None:
        if int(width) * int(height) != len(rays):
            raise SpectralConfigError(
                "width * height must equal the number of rays"
            )


def _material_ior(config: Dict[str, Any], material: Optional[str],
                  wavelength_index: int) -> float:
    if material is None:
        return 1.0
    return float(config["materials"][material]["ior"][wavelength_index])


def snell(angle_deg: float, normal_deg: float, n_i: float,
          n_t: float) -> Dict[str, Any]:
    """Apply Snell's law and report TIR and unpolarized Fresnel."""
    normal = _normalize_angle_180(normal_deg)
    incidence = _normalize_angle_180(angle_deg - normal)
    if abs(incidence) > 90.0:
        # The supplied normal points away from the incident half-space.
        normal = _normalize_angle_180(normal + 180.0)
        incidence = _normalize_angle_180(angle_deg - normal)

    eta = n_i / n_t
    sin_transmitted = eta * math.sin(math.radians(incidence))
    cos_i = math.cos(math.radians(incidence))

    if abs(sin_transmitted) >= 1.0:
        reflected = _normalize_angle_180(normal - incidence)
        return {
            "tir": True,
            "angle": reflected,
            "theta_i": incidence,
            "theta_t": None,
            "eta": eta,
            "fresnel": 1.0,
            "normal": normal,
        }

    theta_t = math.degrees(math.asin(sin_transmitted))
    transmitted = _normalize_angle_180(normal + theta_t)
    cos_t = math.cos(math.radians(theta_t))
    denominator_s = n_i * cos_i + n_t * cos_t
    denominator_p = n_i * cos_t + n_t * cos_i
    r_s = ((n_i * cos_i - n_t * cos_t) / denominator_s) ** 2
    r_p = ((n_i * cos_t - n_t * cos_i) / denominator_p) ** 2
    fresnel = 0.5 * (r_s + r_p)
    return {
        "tir": False,
        "angle": transmitted,
        "theta_i": incidence,
        "theta_t": theta_t,
        "eta": eta,
        "fresnel": fresnel,
        "normal": normal,
    }


def _choose_index(rng: random.Random, pdf: Sequence[float]) -> int:
    u = rng.random()
    cumulative = 0.0
    for index, value in enumerate(pdf):
        cumulative += value
        if u <= cumulative:
            return index
    return len(pdf) - 1


def _scatter_branches(event: Dict[str, Any]) -> List[Dict[str, Any]]:
    if event.get("branches") is not None:
        return event.get("branches")
    return [event]


def trace_path(config: Dict[str, Any], ray: Dict[str, Any],
               wavelength_index: int,
               rng: Optional[random.Random] = None,
               deterministic: bool = False) -> Dict[str, Any]:
    """Trace one ordered path for one discrete wavelength.

    A boundary changes the medium stack only when transmission occurs.  TIR
    reflects and leaves the stack unchanged.  Distance events evaluate
    Beer-Lambert transmittance; their deterministic proposal PDF is one.
    """
    materials = config.get("materials", {})
    angle = float(ray.get("angle", 0.0))
    x = float(ray.get("x", 0.0))
    y = float(ray.get("y", 0.0))
    stack: List[str] = []
    transmission = 1.0
    boundary_throughput = 1.0
    scatter_throughput = 1.0
    scatter_pdf = 1.0
    # Ordinary Beer-Lambert distance evaluation has proposal PDF one.
    # Optional truncated-exponential distance sampling and Fresnel branch
    # sampling replace the corresponding factors below.
    transmission_pdf = 1.0
    boundary_events = 0
    tir_count = 0
    active = True
    termination_reason = None
    event_log: List[Dict[str, Any]] = []

    for event in ray.get("events", []):
        if not active:
            break
        etype = event["type"]
        if etype in ("enter", "exit"):
            current = stack[-1] if stack else None
            if etype == "enter":
                target = event["material"]
                parent = stack[-2] if len(stack) >= 2 else None
            else:
                if not stack:
                    raise SpectralConfigError("exit outside any medium")
                target = stack[-2] if len(stack) >= 2 else None
                if event.get("material", stack[-1]) != stack[-1]:
                    raise SpectralConfigError("nested medium stack mismatch")
                parent = stack[-1]

            n_i = _material_ior(
                config, parent if etype == "enter" else current,
                wavelength_index
            )
            n_t = _material_ior(config, target, wavelength_index)
            boundary = snell(angle, float(event.get("normal", 0.0)), n_i, n_t)
            boundary_events += 1
            sample_fresnel = bool(event.get("sample_fresnel", False))
            reflected_event = False
            reflected_pdf = None
            transmitted_pdf = None
            if sample_fresnel and not boundary["tir"]:
                if deterministic:
                    reflected_pdf = boundary["fresnel"]
                    transmitted_pdf = 1.0 - reflected_pdf
                else:
                    if rng is None:
                        raise SpectralConfigError(
                            "rng required for Fresnel transmission sampling"
                        )
                    reflected_pdf = boundary["fresnel"]
                    transmitted_pdf = 1.0 - reflected_pdf
                    reflected_event = rng.random() < reflected_pdf
                    if transmitted_pdf <= 0.0:
                        reflected_event = True
            entry = {
                "type": etype,
                "event_material": event.get("material"),
                "current_material": current or AIR_NAME,
                "target_material": target or AIR_NAME,
                "theta_i": boundary["theta_i"],
                "theta_t": boundary["theta_t"],
                "fresnel": boundary["fresnel"],
                "tir": boundary["tir"],
                "sample_fresnel": sample_fresnel,
                "reflection_pdf": reflected_pdf,
                "transmission_pdf": transmitted_pdf,
                "sampled_reflection": reflected_event,
                "stack_before": list(stack),
            }
            angle = boundary["angle"]
            if boundary["tir"] or reflected_event:
                tir_count += int(boundary["tir"])
                if reflected_event:
                    # A sampled Fresnel reflection is not TIR; it keeps the
                    # same stack and uses the reflection law from snell().
                    boundary_throughput *= boundary["fresnel"] / reflected_pdf
                    transmission_pdf *= reflected_pdf
                    active = False
                    termination_reason = "fresnel_reflection"
            else:
                if sample_fresnel:
                    if deterministic:
                        boundary_throughput *= transmitted_pdf
                    else:
                        boundary_throughput *= 1.0 / transmitted_pdf
                    transmission_pdf *= transmitted_pdf
                if etype == "enter":
                    stack.append(event["material"])
                else:
                    stack.pop()
            entry["stack_after"] = list(stack)
            event_log.append(entry)

        elif etype == "distance":
            material = stack[-1] if stack else None
            mu = 0.0 if material is None else float(
                materials[material]["absorb"][wavelength_index]
            )
            length = float(event["length"])
            sampled_transmission = bool(event.get("sample_transmission", False))
            sample_rate = float(event.get("sample_rate", mu))
            physical_transmission = math.exp(-mu * length)
            if sampled_transmission and not deterministic:
                if rng is None:
                    raise SpectralConfigError(
                        "rng required for transmission distance sampling"
                    )
                u = rng.random()
                cumulative_collision = 1.0 - math.exp(-sample_rate * length)
                if u < cumulative_collision:
                    segment_pdf = cumulative_collision
                    sampled_length = -math.log1p(-u) / sample_rate
                    physical_transmission = 0.0
                    transmission = 0.0
                    active = False
                    termination_reason = "absorption_collision"
                else:
                    segment_pdf = math.exp(-sample_rate * length)
                    sampled_length = length
                    physical_transmission = math.exp(-mu * length)
                transmission *= physical_transmission / segment_pdf
                transmission_pdf *= segment_pdf
                actual_length = sampled_length
                segment_t = physical_transmission / segment_pdf
                distance_pdf = segment_pdf
            else:
                segment_t = math.exp(-mu * length)
                transmission *= segment_t
                actual_length = length
                distance_pdf = 1.0
            radians = math.radians(angle)
            x += actual_length * math.sin(radians)
            y += actual_length * math.cos(radians)
            event_log.append({
                "type": "distance",
                "material": material or AIR_NAME,
                "length": actual_length,
                "requested_length": length,
                "sampled_transmission": sampled_transmission,
                "sample_rate": sample_rate if sampled_transmission else mu,
                "absorption_coefficient": mu,
                "physical_transmission": (
                    physical_transmission if sampled_transmission else segment_t
                ),
                "termination_reason": (
                    termination_reason if sampled_transmission else None
                ),
                "transmission": segment_t,
                "proposal_pdf": distance_pdf,
                "x": x,
                "y": y,
                "stack_after": list(stack),
            })

        elif etype in ("scatter", "path_end"):
            branches = _scatter_branches(event)
            pdfs = [float(branch.get("pdf", 1.0)) for branch in branches]
            if deterministic:
                directional = [b for b in branches if "angle" in b]
                if len(directional) > 1:
                    raise SpectralConfigError(
                        "deterministic replay cannot merge directional branches"
                    )
                scatter_throughput *= sum(
                    float(branch.get("weight", 1.0)) for branch in branches
                )
                if directional:
                    angle = float(directional[0]["angle"])
                selected = None
                branch_pdf = 1.0
            else:
                if rng is None:
                    raise SpectralConfigError("rng required for scatter sampling")
                selected = _choose_index(rng, pdfs)
                branch = branches[selected]
                branch_pdf = pdfs[selected]
                weight = float(branch.get("weight", 1.0))
                scatter_throughput *= weight / branch_pdf
                scatter_pdf *= branch_pdf
                if "angle" in branch:
                    angle = float(branch["angle"])
            event_log.append({
                "type": etype,
                "selected_branch": selected,
                "branch_pdf": branch_pdf,
                "scatter_pdf_product": scatter_pdf,
                "throughput": scatter_throughput,
                "stack_after": list(stack),
            })
        else:  # Validation already guards this, but keep tracing safe.
            raise SpectralConfigError(f"unsupported event {etype!r}")

    return {
        "angle": angle,
        "x": x,
        "y": y,
        "stack": list(stack),
        "transmission": transmission,
        "boundary_throughput": boundary_throughput,
        "scatter_throughput": scatter_throughput,
        "scatter_pdf": scatter_pdf,
        "transmission_pdf": transmission_pdf,
        "boundary_events": boundary_events,
        "active": active,
        "termination_reason": termination_reason,
        "tir": tir_count > 0,
        "tir_count": tir_count,
        "events": event_log,
    }


def _light_tables(ray: Dict[str, Any], m: int) -> Tuple[List[List[float]],
                                                        List[float],
                                                        List[float]]:
    lights = ray.get("lights", [])
    if not lights:
        return [[1.0] * m], [1.0], [1.0]
    spectra = [_as_float_list(light["spectrum"], "light.spectrum")
               for light in lights]
    weights = [float(light.get("weight", 1.0)) for light in lights]
    pdfs = [float(light.get("pdf", 1.0)) for light in lights]
    return spectra, weights, pdfs


def _deterministic_ray(config: Dict[str, Any], ray: Dict[str, Any],
                       index: int) -> Dict[str, Any]:
    m = len(config["wavelengths"])
    q = config.get("wavelength_weights", [1.0 / m] * m)
    basis = config["xyz_basis"]
    spectra, light_weights, light_pdfs = _light_tables(ray, m)

    transmissions: List[float] = []
    angles: List[float] = []
    positions: List[Dict[str, float]] = []
    logs: List[List[Dict[str, Any]]] = []
    spectral_radiance: List[float] = []
    tir = False
    tir_count = 0
    final_stack: List[str] = []

    for li in range(m):
        traced = trace_path(config, ray, li, deterministic=True)
        emissions = sum(
            light_weights[k] * spectra[k][li] for k in range(len(spectra))
        )
        radiance = (
            emissions
            * traced["scatter_throughput"]
            * traced["boundary_throughput"]
            * traced["transmission"]
        )
        spectral_radiance.append(radiance)
        transmissions.append(traced["transmission"])
        angles.append(traced["angle"])
        positions.append({"x": traced["x"], "y": traced["y"]})
        logs.append(traced["events"])
        tir = tir or traced["tir"]
        tir_count += traced["tir_count"]
        final_stack = traced["stack"]

    energy = sum(q[li] * spectral_radiance[li] for li in range(m))
    xyz = [
        sum(q[li] * basis[li][channel] * spectral_radiance[li]
            for li in range(m))
        for channel in range(3)
    ]
    return {
        "index": index,
        "id": ray.get("id", index),
        "roi": ray.get("roi"),
        "mode": "deterministic_wavelength_quadrature",
        "transmission": transmissions,
        "exit_angles": angles,
        "positions": positions,
        "tir": tir,
        "tir_count": tir_count,
        "final_stack": final_stack,
        "spectral_radiance": spectral_radiance,
        "energy": energy,
        "xyz": xyz,
        "event_log": logs,
        "sampling": {
            "wavelength_pdf": config.get("wavelength_pdf", [1.0 / m] * m),
            "light_pdf": light_pdfs,
            "joint_pdf_used": False,
        },
        "reference": {
            "energy": energy,
            "xyz": list(xyz),
            "transmission": list(transmissions),
            "exit_angles": list(angles),
            "positions": positions,
        },
        "stats": {
            "samples": 0,
            "energy_mean": energy,
            "energy_variance": 0.0,
            "energy_standard_error": 0.0,
            "energy_bias": 0.0,
            "energy_relative_bias": 0.0,
            "energy_noise": 0.0,
            "xyz_mean": list(xyz),
            "xyz_variance": [0.0, 0.0, 0.0],
            "xyz_standard_error": [0.0, 0.0, 0.0],
            "xyz_bias": [0.0, 0.0, 0.0],
        },
    }


def _update_welford(mean: List[float], m2: List[float], count: int,
                    sample: Sequence[float]) -> None:
    for channel, value in enumerate(sample):
        delta = value - mean[channel]
        mean[channel] += delta / count
        m2[channel] += delta * (value - mean[channel])


def _stochastic_ray(config: Dict[str, Any], ray: Dict[str, Any],
                    index: int) -> Dict[str, Any]:
    m = len(config["wavelengths"])
    spp = int(config.get("spp", 0))
    seed = str(config.get("seed", 0))
    wavelength_pdf = _as_float_list(
        config.get("wavelength_pdf", [1.0 / m] * m), "wavelength_pdf"
    )
    q = _as_float_list(config.get("wavelength_weights", [1.0 / m] * m),
                       "wavelength_weights")
    basis = config["xyz_basis"]
    spectra, light_weights, light_pdfs = _light_tables(ray, m)
    reference = _deterministic_ray(config, ray, index)

    energy_mean = 0.0
    energy_m2 = 0.0
    xyz_mean = [0.0, 0.0, 0.0]
    xyz_m2 = [0.0, 0.0, 0.0]
    joint_pdf_sum = 0.0

    trans_sum = [0.0] * m
    trans_count = [0] * m
    angle_sum = [0.0] * m
    x_sum = [0.0] * m
    y_sum = [0.0] * m
    tir_count = 0

    for sample_index in range(spp):
        # The key contains only pixel and sample identity.  Tile id, worker
        # id and completion order are deliberately absent.
        rng = random.Random(f"{seed}:{index}:{sample_index}")
        li = _choose_index(rng, wavelength_pdf)
        light_index = _choose_index(rng, light_pdfs)
        traced = trace_path(config, ray, li, rng=rng, deterministic=False)

        p_wave = wavelength_pdf[li]
        p_light = light_pdfs[light_index]
        p_scatter = traced["scatter_pdf"]
        p_transmission = traced["transmission_pdf"]
        joint_pdf = p_wave * p_light * p_scatter * p_transmission
        joint_pdf_sum += joint_pdf

        light_estimate = (
            light_weights[light_index] * spectra[light_index][li] / p_light
        )
        # q is the target spectral quadrature measure.  The remaining factors
        # divide the complete joint proposal density.
        energy = (
            q[li] / p_wave
            * light_estimate
            * traced["scatter_throughput"]
            * traced["boundary_throughput"]
            * traced["transmission"]
        )
        sample_xyz = [basis[li][channel] * energy for channel in range(3)]

        count = sample_index + 1
        delta = energy - energy_mean
        energy_mean += delta / count
        energy_m2 += delta * (energy - energy_mean)
        _update_welford(xyz_mean, xyz_m2, count, sample_xyz)

        trans_sum[li] += traced["transmission"]
        angle_sum[li] += traced["angle"]
        x_sum[li] += traced["x"]
        y_sum[li] += traced["y"]
        trans_count[li] += 1
        tir_count += traced["tir_count"]

    variance = energy_m2 / (spp - 1) if spp > 1 else 0.0
    sem = math.sqrt(variance / spp) if spp > 1 else 0.0
    xyz_variance = [value / (spp - 1) if spp > 1 else 0.0 for value in xyz_m2]
    xyz_sem = [math.sqrt(value / spp) if spp > 1 else 0.0
               for value in xyz_variance]
    ref_energy = reference["energy"]
    ref_xyz = reference["xyz"]
    energy_bias = energy_mean - ref_energy
    xyz_bias = [xyz_mean[c] - ref_xyz[c] for c in range(3)]

    transmissions: List[Optional[float]] = []
    angles: List[Optional[float]] = []
    positions: List[Optional[Dict[str, float]]] = []
    for li in range(m):
        if trans_count[li] == 0:
            transmissions.append(None)
            angles.append(None)
            positions.append(None)
        else:
            transmissions.append(trans_sum[li] / trans_count[li])
            angles.append(angle_sum[li] / trans_count[li])
            positions.append({
                "x": x_sum[li] / trans_count[li],
                "y": y_sum[li] / trans_count[li],
            })

    return {
        "index": index,
        "id": ray.get("id", index),
        "roi": ray.get("roi"),
        "mode": "monte_carlo_spectral",
        "transmission": transmissions,
        "exit_angles": angles,
        "positions": positions,
        "tir": tir_count > 0,
        "tir_count": tir_count,
        "final_stack": reference["final_stack"],
        "energy": energy_mean,
        "xyz": xyz_mean,
        "reference": {
            "energy": ref_energy,
            "xyz": list(ref_xyz),
            "transmission": reference["transmission"],
            "exit_angles": reference["exit_angles"],
            "positions": reference["positions"],
        },
        "wavelength_counts": trans_count,
        "sampling": {
            "wavelength_pdf": wavelength_pdf,
            "light_pdf": light_pdfs,
            "mean_joint_pdf": joint_pdf_sum / spp if spp else None,
            "transmission_pdf_product": 1.0,
        },
        "stats": {
            "samples": spp,
            "energy_mean": energy_mean,
            "energy_variance": variance,
            "energy_standard_error": sem,
            "energy_bias": energy_bias,
            "energy_relative_bias": (
                energy_bias / ref_energy if abs(ref_energy) > EPS else 0.0
            ),
            "energy_noise": sem / abs(energy_mean) if abs(energy_mean) > EPS else 0.0,
            "xyz_mean": list(xyz_mean),
            "xyz_variance": xyz_variance,
            "xyz_standard_error": xyz_sem,
            "xyz_bias": xyz_bias,
        },
    }


def render_ray(config: Dict[str, Any], index: int) -> Dict[str, Any]:
    ray = config["rays"][index]
    if int(config.get("spp", 0)) <= 0:
        return _deterministic_ray(config, ray, index)
    return _stochastic_ray(config, ray, index)


def _render_ray_worker(config: Dict[str, Any], index: int) -> Dict[str, Any]:
    return render_ray(config, index)


def xyz_to_srgb(xyz: Sequence[float]) -> List[float]:
    """Linear XYZ to display sRGB; only the display result is clamped."""
    x, y, z = xyz
    linear = [
        3.2406 * x - 1.5372 * y - 0.4986 * z,
        -0.9689 * x + 1.8758 * y + 0.0415 * z,
        0.0557 * x - 0.2040 * y + 1.0570 * z,
    ]
    out = []
    for value in linear:
        value = max(0.0, min(1.0, value))
        if value <= 0.0031308:
            out.append(12.92 * value)
        else:
            out.append(1.055 * value ** (1.0 / 2.4) - 0.055)
    return out


def _scene_stats(ray_results: List[Dict[str, Any]]) -> Dict[str, Any]:
    n = len(ray_results)
    mean_energy = sum(r["stats"]["energy_mean"] for r in ray_results) / n
    ref_energy = sum(r["reference"]["energy"] for r in ray_results) / n
    mean_variance = sum(r["stats"]["energy_variance"] for r in ray_results) / n
    mean_sem = sum(r["stats"]["energy_standard_error"]
                   for r in ray_results) / n
    xyz_mean = [
        sum(r["xyz"][c] for r in ray_results) / n for c in range(3)
    ]
    ref_xyz = [
        sum(r["reference"]["xyz"][c] for r in ray_results) / n
        for c in range(3)
    ]
    xyz_bias = [xyz_mean[c] - ref_xyz[c] for c in range(3)]
    max_xyz_bias = max(abs(value) for value in xyz_bias)
    energy_bias = mean_energy - ref_energy
    return {
        "ray_count": n,
        "samples_per_ray": ray_results[0]["stats"]["samples"],
        "mean_energy": mean_energy,
        "reference_energy": ref_energy,
        "total_energy": sum(r["stats"]["energy_mean"] for r in ray_results),
        "reference_total_energy": sum(
            r["reference"]["energy"] for r in ray_results
        ),
        "energy_bias": energy_bias,
        "energy_relative_bias": (
            energy_bias / ref_energy if abs(ref_energy) > EPS else 0.0
        ),
        "mean_energy_variance": mean_variance,
        "mean_energy_standard_error": mean_sem,
        "mean_relative_noise": (
            mean_sem / abs(mean_energy) if abs(mean_energy) > EPS else 0.0
        ),
        "mean_xyz": xyz_mean,
        "reference_xyz": ref_xyz,
        "xyz_bias": xyz_bias,
        "max_abs_xyz_bias": max_xyz_bias,
    }


def _buffer_digest(image_xyz: List[List[float]]) -> str:
    encoded = json.dumps(image_xyz, sort_keys=True,
                         separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def render(config: Dict[str, Any], tile: int = 4, workers: int = 1,
           tile_order: str = "forward") -> Dict[str, Any]:
    """Render all rays and assemble linear XYZ and display sRGB buffers."""
    if tile < 1:
        raise SpectralConfigError("tile must be >= 1")
    if workers < 1:
        raise SpectralConfigError("workers must be >= 1")
    if tile_order not in ("forward", "reverse", "reversed"):
        raise SpectralConfigError("tile_order must be forward or reverse")
    validate_config(config)

    count = len(config["rays"])
    tiles = [list(range(start, min(start + tile, count)))
             for start in range(0, count, tile)]
    if tile_order in ("reverse", "reversed"):
        tiles.reverse()

    results: List[Optional[Dict[str, Any]]] = [None] * count
    if workers == 1:
        for tile_indices in tiles:
            for index in tile_indices:
                results[index] = render_ray(config, index)
    else:
        # Tasks carry stable ray indices.  Completion order cannot change the
        # output, and each ray's RNG is independent of worker identity.  Some
        # restricted sandboxes do not provide the semaphores required by a
        # process pool; deterministically fall back instead of changing math.
        try:
            pool = ProcessPoolExecutor(max_workers=workers)
        except (PermissionError, OSError):
            for tile_indices in tiles:
                for index in tile_indices:
                    results[index] = render_ray(config, index)
        else:
            with pool:
                futures = {}
                for tile_indices in tiles:
                    for index in tile_indices:
                        futures[pool.submit(_render_ray_worker, config, index)] = index
                for future in as_completed(futures):
                    index = futures[future]
                    results[index] = future.result()

    ray_results = [result for result in results if result is not None]
    image_xyz = [result["xyz"] for result in ray_results]
    image_srgb = [xyz_to_srgb(value) for value in image_xyz]
    width = int(config.get("width", len(ray_results)))
    height = int(config.get("height", 1))
    if width * height != len(ray_results):
        raise SpectralConfigError("image dimensions do not match ray count")

    stats = _scene_stats(ray_results)
    digest = _buffer_digest(image_xyz)
    return {
        "rays": ray_results,
        "image": image_xyz,
        "image_xyz": image_xyz,
        "image_srgb": image_srgb,
        "image_shape": {"height": height, "width": width},
        "stats": stats,
        "buffer_digest": digest,
        "sampling_summary": {
            "mode": ray_results[0]["mode"],
            "seed": str(config.get("seed", 0)),
            "spp": int(config.get("spp", 0)),
            "tile": tile,
            "workers": workers,
            "tile_order": tile_order,
        },
    }


def roi_xyz_mean(result: Dict[str, Any], roi: str) -> List[float]:
    rows = [ray["xyz"] for ray in result["rays"] if ray.get("roi") == roi]
    if not rows:
        raise SpectralConfigError(f"ROI {roi!r} has no pixels")
    return [sum(row[c] for row in rows) / len(rows) for c in range(3)]


def write_ppm(path: Path, width: int, height: int,
              rgb: List[List[float]]) -> None:
    if len(rgb) != width * height:
        raise SpectralConfigError("PPM pixel count mismatch")
    data = bytearray(f"P6\n{width} {height}\n255\n".encode("ascii"))
    for pixel in rgb:
        for channel in pixel:
            data.append(round(max(0.0, min(1.0, float(channel))) * 255))
    path.write_bytes(bytes(data))


def write_pgm(path: Path, width: int, height: int, gray: List[float],
              fixed_scale: float = 0.1) -> None:
    if len(gray) != width * height:
        raise SpectralConfigError("PGM pixel count mismatch")
    data = bytearray(f"P5\n{width} {height}\n255\n".encode("ascii"))
    for value in gray:
        normalized = max(0.0, min(1.0, float(value) / fixed_scale))
        data.append(round(normalized * 255))
    path.write_bytes(bytes(data))


def _base_spectral_scene() -> Dict[str, Any]:
    return {
        "wavelengths": [430, 480, 530, 580, 630, 680],
        "xyz_basis": [
            [0.20, 0.11, 0.70], [0.15, 0.25, 0.80],
            [0.20, 0.80, 0.15], [0.70, 0.80, 0.10],
            [0.90, 0.30, 0.05], [0.80, 0.15, 0.02],
        ],
        "materials": {
            "glass": {
                "ior": [1.56, 1.55, 1.54, 1.53, 1.52, 1.51],
                "absorb": [0.10, 0.20, 0.05, 0.03, 0.02, 0.04],
            },
            "inner": {
                "ior": [1.72] * 6,
                "absorb": [0.04] * 6,
            },
        },
    }


def _analytic_scene() -> Dict[str, Any]:
    scene = _base_spectral_scene()
    scene.update({
        "seed": 0,
        "spp": 0,
        "rays": [
            {
                "id": "uniform",
                "angle": 0.0,
                "events": [
                    {"type": "enter", "material": "glass", "normal": 0.0},
                    {"type": "distance", "length": 2.0},
                ],
                "lights": [],
            },
            {
                "id": "refract",
                "angle": 30.0,
                "events": [
                    {"type": "enter", "material": "glass", "normal": 0.0},
                ],
                "lights": [],
            },
            {
                "id": "tir",
                "angle": 0.0,
                "events": [
                    {"type": "enter", "material": "glass", "normal": 0.0},
                    {"type": "exit", "material": "glass", "normal": 45.0},
                ],
                "lights": [],
            },
            {
                "id": "nested",
                "angle": 30.0,
                "events": [
                    {"type": "enter", "material": "glass", "normal": 0.0},
                    {"type": "enter", "material": "inner", "normal": 0.0},
                    {"type": "distance", "length": 1.0},
                    {"type": "exit", "material": "inner", "normal": 0.0},
                    {"type": "distance", "length": 1.0},
                    {"type": "exit", "material": "glass", "normal": -9.0},
                ],
                "lights": [],
            },
        ],
    })
    return scene


def _diffuse_scene(spp: int, seed: int) -> Dict[str, Any]:
    scene = _base_spectral_scene()
    rays = []
    for pixel in range(16):
        rays.append({
            "id": f"box-{pixel}",
            "roi": "diffuse_box",
            "angle": 0.0,
            "events": [
                {"type": "scatter", "weight": 0.8, "pdf": 1.0},
                {"type": "distance", "length": 1.0},
            ],
            "lights": [
                {
                    "spectrum": [0.30, 0.35, 0.40, 0.45, 0.50, 0.48],
                    "weight": 0.7,
                    "pdf": 0.6,
                },
                {
                    "spectrum": [0.20, 0.25, 0.30, 0.34, 0.38, 0.42],
                    "weight": 0.3,
                    "pdf": 0.4,
                },
            ],
        })
    scene.update({"seed": seed, "spp": spp, "width": 4, "height": 4,
                  "rays": rays})
    return scene


def _prism_scene(spp: int, seed: int) -> Dict[str, Any]:
    scene = _base_spectral_scene()
    events = [
        {"type": "enter", "material": "glass", "normal": 0.0},
        {"type": "distance", "length": 1.0},
        {"type": "exit", "material": "glass", "normal": -10.0},
        {"type": "distance", "length": 2.0},
    ]
    base_ray = {"id": "position-probe", "angle": 35.0, "events": events,
                "lights": []}
    validate_scene = dict(scene)
    validate_scene["rays"] = [base_ray]
    validate_config(validate_scene)
    x_positions = []
    for li in range(len(scene["wavelengths"])):
        traced = trace_path(validate_scene, base_ray, li, deterministic=True)
        x_positions.append(traced["x"])
    lo, hi = min(x_positions), max(x_positions)
    width, height = 24, 3
    centers = [
        0.0 if hi == lo else
        (x - lo) / (hi - lo) * (width - 1) for x in x_positions
    ]

    rays = []
    for row in range(height):
        for column in range(width):
            spectrum = [
                math.exp(-0.5 * ((column - centers[li]) / 1.15) ** 2)
                for li in range(len(scene["wavelengths"]))
            ]
            rays.append({
                "id": f"prism-{row}-{column}",
                "roi": "prism",
                "x": float(column),
                "y": float(row),
                "angle": 35.0,
                "events": events,
                "lights": [{
                    "spectrum": spectrum,
                    "weight": 1.0,
                    "pdf": 1.0,
                }],
            })
    scene.update({
        "seed": seed,
        "spp": spp,
        "width": width,
        "height": height,
        "rays": rays,
    })
    return scene


def _analytic_validation(scene: Dict[str, Any]) -> Tuple[Dict[str, Any],
                                                         List[Dict[str, Any]]]:
    result = render(scene, tile=2, workers=1)
    by_id = {ray["id"]: ray for ray in result["rays"]}
    checks: List[Dict[str, Any]] = []

    def check(name: str, ok: bool, details: Dict[str, Any]) -> None:
        checks.append({"name": name, "passed": bool(ok), "details": details})

    glass = scene["materials"]["glass"]
    uniform = by_id["uniform"]
    for li, mu in enumerate(glass["absorb"]):
        expected = math.exp(-mu * 2.0)
        relative = abs(uniform["transmission"][li] - expected) / expected
        check(f"uniform_transmission_{li}", relative <= 0.01,
              {"expected": expected, "measured": uniform["transmission"][li],
               "relative_error": relative})

    refract = by_id["refract"]
    for li, ior in enumerate(glass["ior"]):
        expected = math.degrees(math.asin(math.sin(math.radians(30.0)) / ior))
        error = abs(refract["exit_angles"][li] - expected)
        check(f"single_refraction_{li}", error <= 0.5,
              {"expected_deg": expected,
               "measured_deg": refract["exit_angles"][li],
               "absolute_error_deg": error})

    tir = by_id["tir"]
    check("total_internal_reflection",
          tir["tir"] and tir["tir_count"] == len(scene["wavelengths"])
          and tir["final_stack"] == ["glass"],
          {"exit_angles": tir["exit_angles"], "tir_count": tir["tir_count"],
           "final_stack": tir["final_stack"]})

    nested = by_id["nested"]
    for li in range(len(scene["wavelengths"])):
        expected = (
            math.exp(-scene["materials"]["inner"]["absorb"][li] * 1.0)
            * math.exp(-glass["absorb"][li] * 1.0)
        )
        relative = abs(nested["transmission"][li] - expected) / expected
        check(f"nested_glass_transmission_{li}", relative <= 0.01,
              {"expected": expected,
               "measured": nested["transmission"][li],
               "relative_error": relative})
    check("nested_stack_closed", nested["final_stack"] == [],
          {"final_stack": nested["final_stack"]})
    return result, checks


def _diffuse_validation(workers: int) -> Tuple[Dict[str, Any],
                                               List[Dict[str, Any]]]:
    low = render(_diffuse_scene(256, 1234), tile=4, workers=workers)
    high = render(_diffuse_scene(4096, 987654), tile=8, workers=workers)
    low_mean = roi_xyz_mean(low, "diffuse_box")
    high_mean = roi_xyz_mean(high, "diffuse_box")
    diff = [low_mean[c] - high_mean[c] for c in range(3)]
    max_error = max(abs(value) for value in diff)
    check = {
        "name": "diffuse_box_roi_linear_xyz",
        "passed": max_error <= 0.02,
        "details": {
            "low_spp": 256,
            "reference_spp": 4096,
            "low_linear_xyz_mean": low_mean,
            "reference_linear_xyz_mean": high_mean,
            "absolute_error": diff,
            "max_abs_error": max_error,
            "threshold": 0.02,
        },
    }
    summary = {
        "low": low["stats"],
        "independent_high_sample": high["stats"],
        "roi": "diffuse_box",
    }
    return summary, [check]


def _prism_validation(outdir: Path, workers: int) -> Tuple[Dict[str, Any],
                                                           List[Dict[str, Any]]]:
    seeds = [101, 202, 303, 404]
    spp = 120
    reference_scene = _prism_scene(0, 0)
    reference = render(reference_scene, tile=8, workers=1)
    width = reference_scene["width"]
    height = reference_scene["height"]
    ref_ray = reference["rays"][0]
    short_x = ref_ray["reference"]["positions"][0]["x"]
    long_x = ref_ray["reference"]["positions"][-1]["x"]

    checks: List[Dict[str, Any]] = []
    energy_stats: Dict[str, Any] = {}
    tile_summary: Dict[str, Any] = {}

    def check(name: str, ok: bool, details: Dict[str, Any]) -> None:
        checks.append({"name": name, "passed": bool(ok), "details": details})

    check("prism_short_wave_more_strongly_displaced",
          short_x > long_x,
          {"short_430_x": short_x, "long_680_x": long_x})

    for seed in seeds:
        scene = _prism_scene(spp, seed)
        forward = render(scene, tile=4, workers=workers,
                         tile_order="forward")
        reversed_order = render(scene, tile=4, workers=workers,
                                tile_order="reverse")
        ray = forward["rays"][0]
        short = ray["positions"][0]
        long = ray["positions"][-1]
        short_count = ray["wavelength_counts"][0]
        long_count = ray["wavelength_counts"][-1]
        check(f"prism_seed_{seed}_endpoints_sampled",
              short is not None and long is not None
              and short_count > 0 and long_count > 0,
              {"short_count": short_count, "long_count": long_count})
        if short is not None and long is not None:
            check(f"prism_seed_{seed}_endpoint_positions",
                  abs(short["x"] - short_x) <= 1.0e-12
                  and abs(long["x"] - long_x) <= 1.0e-12
                  and short["x"] > long["x"],
                  {"short_430_x": short["x"], "long_680_x": long["x"],
                   "reference_short_x": short_x,
                   "reference_long_x": long_x})

        write_ppm(outdir / f"prism_spectral_seed_{seed}.ppm", width, height,
                  forward["image_srgb"])
        differences = [
            sum(abs(forward["image_xyz"][i][c]
                    - reference["image_xyz"][i][c]) for c in range(3)) / 3.0
            for i in range(len(forward["image_xyz"]))
        ]
        (outdir / f"prism_difference_seed_{seed}.json").write_text(
            json.dumps({"difference_linear_xyz_mean_abs": differences,
                        "max": max(differences),
                        "mean": sum(differences) / len(differences),
                        "reference_spp": 0}, sort_keys=True, indent=2)
            + "\n"
        )
        write_pgm(outdir / f"prism_difference_seed_{seed}.pgm",
                  width, height, differences)
        (outdir / f"prism_seed_{seed}.json").write_text(
            json.dumps(forward, sort_keys=True, allow_nan=False, indent=2)
            + "\n"
        )
        energy_stats[str(seed)] = forward["stats"]
        digest_equal = (forward["buffer_digest"]
                        == reversed_order["buffer_digest"])
        stats_equal = forward["stats"] == reversed_order["stats"]
        image_equal = forward["image_xyz"] == reversed_order["image_xyz"]
        tile_summary[str(seed)] = {
            "forward_digest": forward["buffer_digest"],
            "reverse_digest": reversed_order["buffer_digest"],
            "digest_equal": digest_equal,
            "stats_equal": stats_equal,
            "image_equal": image_equal,
        }
        check(f"prism_seed_{seed}_tile_order_buffer",
              digest_equal and stats_equal and image_equal,
              tile_summary[str(seed)])

    write_ppm(outdir / "prism_spectral_reference.ppm", width, height,
              reference["image_srgb"])
    (outdir / "prism_reference.json").write_text(
        json.dumps(reference, sort_keys=True, allow_nan=False, indent=2)
        + "\n"
    )
    (outdir / "prism_tile_order_summary.json").write_text(
        json.dumps(tile_summary, sort_keys=True, indent=2) + "\n"
    )
    summary = {
        "spp": spp,
        "seeds": seeds,
        "shape": {"height": height, "width": width},
        "energy_stats": energy_stats,
        "reference_stats": reference["stats"],
        "tile_order": tile_summary,
    }
    return summary, checks


def run_validation(outdir: str = "reports", workers: int = 2) -> Dict[str, Any]:
    out = Path(outdir)
    out.mkdir(parents=True, exist_ok=True)
    all_checks: List[Dict[str, Any]] = []

    analytic_scene = _analytic_scene()
    analytic_result, analytic_checks = _analytic_validation(analytic_scene)
    all_checks.extend(analytic_checks)
    (out / "analytic_scene.json").write_text(
        json.dumps(analytic_scene, sort_keys=True, indent=2) + "\n"
    )
    (out / "analytic_result.json").write_text(
        json.dumps(analytic_result, sort_keys=True, allow_nan=False, indent=2)
        + "\n"
    )

    diffuse_summary, diffuse_checks = _diffuse_validation(workers)
    all_checks.extend(diffuse_checks)

    prism_summary, prism_checks = _prism_validation(out, workers)
    all_checks.extend(prism_checks)

    energy_stats = {
        "analytic": analytic_result["stats"],
        "diffuse_box": diffuse_summary,
        "prism": prism_summary["energy_stats"],
    }
    summary = {
        "passed": all(item["passed"] for item in all_checks),
        "checks": all_checks,
        "thresholds": {
            "relative_transmission": 0.01,
            "absolute_exit_angle_deg": 0.5,
            "roi_linear_xyz_abs": 0.02,
        },
        "energy_statistics": energy_stats,
        "prism": prism_summary,
    }
    (out / "energy_stats.json").write_text(
        json.dumps(energy_stats, sort_keys=True, indent=2) + "\n"
    )
    (out / "validation_summary.json").write_text(
        json.dumps(summary, sort_keys=True, allow_nan=False, indent=2) + "\n"
    )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Spectral offline path-event renderer"
    )
    parser.add_argument("input", nargs="?", type=Path)
    parser.add_argument("output", nargs="?", type=Path)
    parser.add_argument("--tile", type=int, default=4)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--tile-order", choices=["forward", "reverse"],
                        default="forward")
    parser.add_argument("--seed", type=int)
    parser.add_argument("--spp", type=int)
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--outdir", default="reports")
    args = parser.parse_args()

    if args.validate:
        summary = run_validation(args.outdir, max(1, args.workers))
        print(json.dumps({
            "passed": summary["passed"],
            "outdir": args.outdir,
            "checks": len(summary["checks"]),
            "failures": [c for c in summary["checks"] if not c["passed"]],
        }, sort_keys=True, indent=2))
        raise SystemExit(0 if summary["passed"] else 1)

    if args.input is None or args.output is None:
        parser.error("input and output are required unless --validate is used")
    config = json.loads(args.input.read_text())
    if args.seed is not None:
        config["seed"] = args.seed
    if args.spp is not None:
        config["spp"] = args.spp
    result = render(config, tile=args.tile, workers=args.workers,
                    tile_order=args.tile_order)
    args.output.write_text(
        json.dumps(result, sort_keys=True, allow_nan=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    main()
