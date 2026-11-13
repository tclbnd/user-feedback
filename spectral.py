"""Spectral path replay renderer.

Offline spectral transport over replayed interface/distance events:

- Wavelength sampling with reproducible per-(pixel, sample) RNG streams, so
  results are invariant to tile size, tile order and worker count.
- Medium boundary stack for nested dielectrics; Snell refraction at every
  enter/exit interface; total internal reflection (TIR) reflects the ray and
  leaves the medium stack unchanged.
- Beer-Lambert absorption along distance events in the current medium.
- Light-branch sampling shares one weight convention with wavelength
  sampling: every estimator divides by the joint proposal pdf
  (pdf_wavelength * pdf_light). Interface/distance events are deterministic
  replays (pdf == 1) and therefore do not enter the weight.
- Fresnel reflectance is reported per interface but, per spec, the replayed
  transmission only carries medium absorption (no double counting).
- Output is linear XYZ plus an sRGB display transform.
"""
import argparse
import hashlib
import json
import math
import random
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

AIR_IOR = 1.0

# CIE XYZ -> linear sRGB
_XYZ_TO_SRGB = ((3.2406, -1.5372, -0.4986),
                (-0.9689, 1.8758, 0.0415),
                (0.0557, -0.2040, 1.0570))


def _stream_seed(seed, pixel, sample):
    """Deterministic seed per (seed, pixel, sample); schedule independent."""
    h = hashlib.sha256(f"spectral:{seed}:{pixel}:{sample}".encode("utf8")).digest()
    return int.from_bytes(h, "big")


def _ior(materials, name, wi):
    return AIR_IOR if name is None else materials[name]["ior"][wi]


def _absorb(materials, name, wi):
    return 0.0 if name is None else materials[name]["absorb"][wi]


def _interface(angle_deg, normal_deg, n_i, n_t):
    """Snell refraction across one boundary.

    Angles are signed degrees about a fixed reference axis. On TIR the ray is
    mirrored about the interface normal and the caller keeps the medium stack.
    Returns the new absolute angle, the TIR flag and the unpolarized Fresnel
    reflectance (reported only; not folded into transmission).
    """
    theta_i = math.radians(angle_deg - normal_deg)
    sin_t = n_i / n_t * math.sin(theta_i)
    if abs(sin_t) > 1.0:
        return {"angle": 2.0 * normal_deg - angle_deg, "tir": True, "fresnel": 1.0}
    theta_t = math.asin(sin_t)
    cos_i = math.cos(theta_i)
    cos_t = math.cos(theta_t)
    rs = (n_i * cos_i - n_t * cos_t) / (n_i * cos_i + n_t * cos_t)
    rp = (n_i * cos_t - n_t * cos_i) / (n_i * cos_t + n_t * cos_i)
    return {"angle": normal_deg + math.degrees(theta_t),
            "tir": False, "fresnel": 0.5 * (rs * rs + rp * rp)}


def trace_ray(ray, wi, materials):
    """Replay the event list for wavelength index wi.

    enter pushes the material onto the boundary stack, exit pops it (and must
    match the stack top); the refractive index pair is always taken from the
    two media adjacent to the boundary, so nested glass is handled by the
    stack, not by special cases. distance applies Beer-Lambert absorption of
    the current (stack top) medium.
    """
    angle = float(ray["angle"])
    stack = []
    transmission = 1.0
    tir = False
    interfaces = []
    for ev in ray["events"]:
        kind = ev["type"]
        if kind == "distance":
            top = stack[-1] if stack else None
            transmission *= math.exp(-_absorb(materials, top, wi) * float(ev["length"]))
        elif kind in ("enter", "exit"):
            if kind == "enter":
                n_i = _ior(materials, stack[-1] if stack else None, wi)
                n_t = _ior(materials, ev["material"], wi)
            else:
                if not stack or stack[-1] != ev["material"]:
                    raise ValueError(
                        "exit material %r does not match stack %r" % (ev["material"], stack))
                n_i = _ior(materials, stack[-1], wi)
                n_t = _ior(materials, stack[-2] if len(stack) > 1 else None, wi)
            hit = _interface(angle, float(ev["normal"]), n_i, n_t)
            angle = hit["angle"]
            interfaces.append({"type": kind, "material": ev["material"],
                               "tir": hit["tir"], "fresnel": hit["fresnel"]})
            if hit["tir"]:
                tir = True  # reflected: medium stack stays unchanged
            elif kind == "enter":
                stack.append(ev["material"])
            else:
                stack.pop()
        else:
            raise ValueError("unknown event type %r" % (kind,))
    return {"transmission": transmission, "exit_angle": angle,
            "tir": tir, "interfaces": interfaces}


def _pick_light(lights, u):
    acc = 0.0
    for i, light in enumerate(lights):
        acc += light["pdf"]
        if u < acc:
            return i
    return len(lights) - 1


def render_pixel(ray, config, pixel_index):
    """Render one ray (pixel). Returns (public record, xyz_reference, y variance)."""
    materials = config["materials"]
    basis = config["xyz_basis"]
    n_w = len(config["wavelengths"])
    spp = int(config.get("spp", 0))
    seed = config.get("seed", 0)

    traces = [trace_ray(ray, wi, materials) for wi in range(n_w)]
    transmission = [t["transmission"] for t in traces]
    exit_angles = [t["exit_angle"] for t in traces]
    tir = any(t["tir"] for t in traces)
    # Fresnel report: one row per interface event, columns per wavelength.
    n_if = len(traces[0]["interfaces"])
    fresnel = [[traces[wi]["interfaces"][k]["fresnel"] for wi in range(n_w)]
               for k in range(n_if)]

    # Deterministic reference: full enumeration over wavelengths and light
    # branches (this is also the spp -> infinity limit of the estimator).
    xyz_ref = [0.0, 0.0, 0.0]
    for wi in range(n_w):
        light_sum = sum(l["weight"] * l["spectrum"][wi] for l in ray["lights"])
        for c in range(3):
            xyz_ref[c] += basis[wi][c] * transmission[wi] * light_sum
    xyz_ref = [v / n_w for v in xyz_ref]

    if spp > 0:
        acc = [0.0, 0.0, 0.0]
        ys = []
        pdf_w = 1.0 / n_w
        for s in range(spp):
            rng = random.Random(_stream_seed(seed, pixel_index, s))
            wi = rng.randrange(n_w)                    # wavelength proposal, pdf 1/n_w
            li = _pick_light(ray["lights"], rng.random())  # light-branch proposal
            light = ray["lights"][li]
            # Single weight convention: true branch weight / joint proposal pdf.
            w = light["weight"] / (light["pdf"] * pdf_w)
            t = traces[wi]["transmission"]
            contrib = [basis[wi][c] * t * light["spectrum"][wi] * w for c in range(3)]
            for c in range(3):
                acc[c] += contrib[c]
            ys.append(contrib[1])
        xyz = [v / spp for v in acc]
        mean_y = xyz[1]
        var_y = (sum((y - mean_y) ** 2 for y in ys) / (spp - 1)) if spp > 1 else 0.0
    else:
        xyz = xyz_ref
        var_y = 0.0

    record = {"transmission": transmission,
              "exit_angles": exit_angles,
              "tir": tir,
              "fresnel": fresnel,
              "xyz": xyz}
    return record, xyz_ref, var_y


def _xyz_to_srgb(xyz):
    out = []
    for c in range(3):
        v = sum(_XYZ_TO_SRGB[c][k] * xyz[k] for k in range(3))
        v = min(1.0, max(0.0, v))
        out.append(12.92 * v if v <= 0.0031308 else 1.055 * v ** (1.0 / 2.4) - 0.055)
    return out


def _digest(payload):
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf8")).hexdigest()


def render(config, tile=4, workers=1, tile_order="asc"):
    """Render all rays. Per-pixel results depend only on (seed, pixel, sample),
    never on tile size, tile order or worker count."""
    rays = config["rays"]
    n = len(rays)
    tile = max(1, int(tile))
    tiles = [list(range(s, min(s + tile, n))) for s in range(0, n, tile)]
    if tile_order == "desc":
        tiles.reverse()

    def run_tile(indices):
        return [(i, render_pixel(rays[i], config, i)) for i in indices]

    results = {}
    if workers and int(workers) > 1 and len(tiles) > 1:
        with ThreadPoolExecutor(max_workers=int(workers)) as pool:
            for chunk in pool.map(run_tile, tiles):
                results.update(chunk)
    else:
        for t in tiles:
            results.update(run_tile(t))

    ordered = [results[i] for i in range(n)]
    rays_out = [rec for rec, _, _ in ordered]
    image = [rec["xyz"] for rec in rays_out]
    refs = [ref for _, ref, _ in ordered]
    var_ys = [v for _, _, v in ordered]
    spp = int(config.get("spp", 0))

    # Energy, bias and noise are reported separately.
    energy = [sum(px[c] for px in image) for c in range(3)]
    bias = [sum(image[i][c] - refs[i][c] for i in range(n)) / max(1, n)
            for c in range(3)]
    noise = {"y_variance_mean": sum(var_ys) / max(1, n),
             "y_sem_mean": (sum(math.sqrt(v / spp) for v in var_ys) / max(1, n)) if spp else 0.0}
    stats = {"spp": spp,
             "seed": config.get("seed", 0),
             "energy": {"x": energy[0], "y": energy[1], "z": energy[2]},
             "energy_bias": bias,
             "noise": noise,
             "digest": _digest({"image": image, "rays": rays_out})}
    return {"rays": rays_out,
            "image": image,
            "display": [_xyz_to_srgb(px) for px in image],
            "stats": stats}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("input", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--tile", type=int, default=4)
    p.add_argument("--workers", type=int, default=1)
    p.add_argument("--tile-order", choices=["asc", "desc"], default="asc")
    a = p.parse_args()
    out = render(json.loads(a.input.read_text()),
                 tile=a.tile, workers=a.workers, tile_order=a.tile_order)
    a.output.write_text(json.dumps(out, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
