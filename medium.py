"""Boundary stack, wavelength-aware refraction and volumetric absorption.

All angles are signed degrees relative to the fixed scene reference axis,
matching the offline event schema.  A boundary event carries its own signed
normal angle, so the same math covers parallel faces, tilted exits and
nested geometry.

Semantics (see README):

* ``enter`` pushes the named medium onto a stack; ``exit`` pops it.
* At a boundary, Snell's law is applied between the stack top (or air) and
  the next medium, per wavelength.  Total internal reflection reflects the
  ray in place and leaves the stack untouched.
* ``distance`` events apply Beer-Lambert absorption using the current
  medium's coefficient.  No interface Fresnel term is folded into the
  reported transmission; Fresnel is reported separately instead.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import List, Optional

AIR_IOR = 1.0


def norm_angle_deg(angle_deg: float) -> float:
    """Wrap an angle into (-180, 180]."""
    a = (angle_deg + 180.0) % 360.0 - 180.0
    if a == -180.0:
        return 180.0
    return a


def refract_signed(theta_deg: float, normal_deg: float, eta_i: float,
                   eta_t: float):
    """Snell refraction with sign convention preserved.

    Returns (transmitted_angle, tir).  theta and normal are signed degrees.
    The incidence magnitude is measured against the normal; the transmitted
    ray keeps the same side-of-normal sign as the incident ray (opposite
    tangent relative to the oriented normal), expressed in the global frame.
    """
    theta_i = norm_angle_deg(theta_deg - normal_deg)
    sin_i = math.sin(math.radians(theta_i))
    eta = eta_i / eta_t
    sin_t_sq = (eta * sin_i) ** 2
    if sin_t_sq > 1.0:
        # Total internal reflection: mirror across the normal, stack stays.
        theta_r = -theta_i
        return norm_angle_deg(normal_deg + theta_r), True
    sign = 1.0 if sin_i >= 0.0 else -1.0
    theta_t = sign * math.degrees(math.asin(math.sqrt(min(1.0, sin_t_sq))))
    return norm_angle_deg(normal_deg + theta_t), False


def reflect_signed(theta_deg: float, normal_deg: float) -> float:
    """Specular reflection of a signed global angle about a normal."""
    theta_i = norm_angle_deg(theta_deg - normal_deg)
    return norm_angle_deg(normal_deg - theta_i)


def fresnel_unpolarized(eta_i: float, eta_t: float,
                        cos_theta_i: float) -> float:
    """Unpolarised Fresnel reflectance for dielectric media."""
    c = abs(cos_theta_i)
    sin2_t = max(0.0, (eta_i / eta_t) ** 2 * (1.0 - c * c))
    if sin2_t >= 1.0:
        return 1.0
    cos_t = math.sqrt(1.0 - sin2_t)
    rs = ((eta_i * c - eta_t * cos_t) / (eta_i * c + eta_t * cos_t)) ** 2
    rp = ((eta_t * c - eta_i * cos_t) / (eta_t * c + eta_i * cos_t)) ** 2
    return 0.5 * (rs + rp)


def beer_lambert(absorb_coeff: float, length: float) -> float:
    """Transmittance exp(-sigma_a * L); non-negative coefficient assumed."""
    if absorb_coeff <= 0.0 or length <= 0.0:
        return 1.0
    return math.exp(-absorb_coeff * length)


@dataclass
class WaveTrace:
    """Per-wavelength result of replaying an event list."""

    wavelength_index: int
    transmission: float = 1.0
    exit_angle: float = 0.0
    tir: bool = False
    tir_events: List[int] = field(default_factory=list)
    fresnel: List[float] = field(default_factory=list)
    segments: List[dict] = field(default_factory=list)
    stack: List[str] = field(default_factory=list)
    ok: bool = True
    error: Optional[str] = None


def _ior_of(materials: dict, name, wi: int) -> float:
    if name is None:
        return AIR_IOR
    return float(materials[name]["ior"][wi])


def _absorb_of(materials: dict, name, wi: int) -> float:
    if name is None:
        return 0.0
    return float(materials[name]["absorb"][wi])


def _scatter_of(materials: dict, name, wi: int) -> float:
    if name is None:
        return 0.0
    raw = materials[name].get("scatter", 0.0)
    if isinstance(raw, (list, tuple)):
        return float(raw[wi])
    return float(raw)


def trace_events(ray: dict, materials: dict, wi: int) -> WaveTrace:
    """Replay one ray's boundary/distance events for wavelength index wi."""
    tr = WaveTrace(wavelength_index=wi, exit_angle=float(ray["angle"]))
    stack: List[str] = []
    angle = float(ray["angle"])
    for ei, ev in enumerate(ray.get("events", [])):
        kind = ev["type"]
        if kind in ("enter", "exit"):
            current = stack[-1] if stack else None
            if kind == "enter":
                nxt = ev["material"]
            else:
                if not stack:
                    tr.ok = False
                    tr.error = "event %d: exit with empty stack" % ei
                    break
                if ev.get("material") is not None and stack[-1] != ev["material"]:
                    tr.ok = False
                    tr.error = ("event %d: exit material %s does not match "
                                "stack top %s"
                                % (ei, ev["material"], stack[-1]))
                    break
                nxt = None
            eta_i = _ior_of(materials, current, wi)
            eta_t = _ior_of(materials, nxt, wi)
            normal = float(ev.get("normal", 0.0))
            new_angle, tir = refract_signed(angle, normal, eta_i, eta_t)
            cos_i = abs(math.cos(math.radians(
                norm_angle_deg(angle - normal))))
            tr.fresnel.append(fresnel_unpolarized(eta_i, eta_t, cos_i))
            if tir:
                tr.tir = True
                tr.tir_events.append(ei)
                angle = reflect_signed(angle, normal)
                # Stack intentionally unchanged: the ray stays in its medium.
            else:
                angle = new_angle
                if kind == "enter":
                    stack.append(nxt)
                else:
                    stack.pop()
        elif kind == "distance":
            current = stack[-1] if stack else None
            length = float(ev["length"])
            sigma = _absorb_of(materials, current, wi)
            t = beer_lambert(sigma, length)
            tr.transmission *= t
            tr.segments.append(dict(event=ei, medium=current, length=length,
                                    sigma_a=sigma, transmittance=t,
                                    scatter=_scatter_of(materials, current,
                                                        wi)))
        else:
            tr.ok = False
            tr.error = "event %d: unknown type %r" % (ei, kind)
            break
    tr.exit_angle = angle
    tr.stack = list(stack)
    return tr
