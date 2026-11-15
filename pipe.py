#!/usr/bin/env python3
"""Deferred opaque + forward transparent rendering pipeline.

The opaque pass builds a G-buffer only for the nearest opaque fragment.
Transparent fragments never write that G-buffer; they read its depth for
rejection and are composited with premultiplied alpha in linear color space.
"""

import hashlib
import json
import math
import sys
from itertools import permutations

ACCEPTANCE_ERROR = 0.01
SIGNATURE_KEYS = (
    "color",
    "per_light",
    "occ",
    "occlusion",
    "opaque_per_light",
    "transparent_per_light",
    "transparent_net_per_light",
    "transparent_alpha",
)


def clamp(value, low, high):
    return max(low, min(high, value))


def normalize(vector):
    norm = math.sqrt(sum(float(x) * float(x) for x in vector))
    if norm <= 1e-12:
        return [0.0 for _ in vector]
    return [float(x) / norm for x in vector]


def shade_material(base_color, light, normal=None):
    """Shared Lambert lighting for both deferred and forward paths.

    Light dir is the direction from the surface toward the light. All colors
    are treated as linear. A missing normal means a view-facing +Z surface.
    """
    normal = normalize(normal or [0.0, 0.0, 1.0])
    light_dir = normalize(light.get("dir", [0.0, 0.0, 1.0]))
    ndl = max(0.0, sum(normal[c] * light_dir[c] for c in range(3)))
    intensity = float(light.get("intensity", 1.0))
    light_color = light["color"]
    return [
        float(base_color[c]) * float(light_color[c]) * ndl * intensity
        for c in range(3)
    ]


def material_key(obj):
    """Deterministic tie breaker that does not depend on submission order."""
    return json.dumps(
        {
            "albedo": obj.get("albedo"),
            "tint": obj.get("tint"),
            "alpha": obj.get("alpha"),
            "normal": obj.get("normal"),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def tag_objects(objects):
    return [dict(obj, id=obj.get("id", index)) for index, obj in enumerate(objects)]


def build_gbuffer(opaque):
    """Write only the nearest opaque fragment into the G-buffer."""
    if not opaque:
        return {
            "depth": None,
            "albedo": None,
            "normal": None,
            "index": None,
            "occluded_indices": [],
            "transparent_writes": 0,
        }

    ordered = sorted(
        ((obj.get("id", index), obj) for index, obj in enumerate(opaque)),
        key=lambda item: (float(item[1]["z"]), material_key(item[1]), item[0]),
    )
    fragment_id, fragment = ordered[0]
    return {
        "depth": float(fragment["z"]),
        "albedo": [float(x) for x in fragment["albedo"]],
        "normal": fragment.get("normal", [0.0, 0.0, 1.0]),
        "index": fragment_id,
        "occluded_indices": [item[0] for item in ordered[1:]],
        # Explicit invariant: the transparent forward pass has no G-buffer write.
        "transparent_writes": 0,
    }


def composite_transparent(visible_transparent, opaque_albedo, opaque_normal, lights):
    """Composite visible transparent layers over the deferred opaque result."""
    zero = [0.0, 0.0, 0.0]
    opaque_light = (
        [zero[:] for _ in lights]
        if opaque_albedo is None
        else [
            shade_material(opaque_albedo, light, opaque_normal)
            for light in lights
        ]
    )
    transparent_light = [zero[:] for _ in lights]
    coverage = 0.0

    # visible_transparent is ordered far to near for the premultiplied over op.
    for _, fragment in visible_transparent:
        alpha = clamp(float(fragment.get("alpha", 1.0)), 0.0, 1.0)
        normal = fragment.get("normal", [0.0, 0.0, 1.0])
        for light_index, light in enumerate(lights):
            source = [
                value * alpha
                for value in shade_material(fragment["tint"], light, normal)
            ]
            transparent_light[light_index] = [
                source[c] + transparent_light[light_index][c] * (1.0 - alpha)
                for c in range(3)
            ]
        coverage = alpha + coverage * (1.0 - alpha)

    if opaque_albedo is None:
        final_light = [row[:] for row in transparent_light]
        final_alpha = coverage
    else:
        final_light = [
            [
                transparent_light[light_index][c]
                + opaque_light[light_index][c] * (1.0 - coverage)
                for c in range(3)
            ]
            for light_index in range(len(lights))
        ]
        final_alpha = 1.0

    net_transparent = [
        [
            final_light[light_index][c] - opaque_light[light_index][c]
            for c in range(3)
        ]
        for light_index in range(len(lights))
    ]

    return {
        "opaque": opaque_light,
        "transparent": transparent_light,
        "transparent_net": net_transparent,
        "final": final_light,
        "transparent_alpha": coverage,
        "alpha": final_alpha,
    }


def forward_transparent(transparent, gbuffer, lights):
    """Read G-buffer depth, but do not modify or write the G-buffer."""
    depth = gbuffer["depth"]
    visible = []
    depth_rejected = []

    for submit_index, fragment in enumerate(transparent):
        fragment_id = fragment.get("id", submit_index)
        fragment_z = float(fragment["z"])
        # Equal depth is intentionally not rejected: coplanar/contact pixels
        # remain visible instead of being incorrectly discarded.
        if depth is not None and fragment_z > depth:
            depth_rejected.append(fragment_id)
        else:
            visible.append((fragment_id, fragment))

    # Smaller z is nearer. Over compositing needs far to near, hence -z.
    visible.sort(
        key=lambda item: (
            -float(item[1]["z"]),
            material_key(item[1]),
            item[0],
        )
    )

    composite = composite_transparent(
        visible,
        gbuffer["albedo"],
        gbuffer["normal"],
        lights,
    )
    composite["visible_indices"] = [item[0] for item in visible]
    composite["depth_rejected_indices"] = depth_rejected
    return composite


def render_once(opaque, transparent, lights):
    gbuffer = build_gbuffer(opaque)
    forward = forward_transparent(transparent, gbuffer, lights)

    color_rgb = [
        sum(forward["final"][light_index][channel]
            for light_index in range(len(lights)))
        for channel in range(3)
    ]
    transparent_rejected = forward["depth_rejected_indices"]
    opaque_rejected = gbuffer["occluded_indices"]
    occlusion = {
        "opaque_gbuffer_rejected": len(opaque_rejected),
        "transparent_depth_rejected": len(transparent_rejected),
        "total": len(opaque_rejected) + len(transparent_rejected),
    }

    return {
        "color": color_rgb + [forward["alpha"]],
        "per_light": forward["final"],
        # Backward-compatible scalar: transparent fragments rejected by depth.
        "occ": len(transparent_rejected),
        "occlusion": occlusion,
        "opaque_per_light": forward["opaque"],
        "transparent_per_light": forward["transparent"],
        "transparent_net_per_light": forward["transparent_net"],
        "transparent_alpha": forward["transparent_alpha"],
        "gbuffer": gbuffer,
        "occluded_opaque_indices": opaque_rejected,
        "occluded_transparent_indices": transparent_rejected,
    }


def ray_reference(opaque, transparent, lights):
    """Independent ray traversal used to validate the two-pass pipeline."""
    hits = []
    for fragment in opaque:
        hits.append((float(fragment["z"]), 1, fragment.get("id"), fragment))
    for fragment in transparent:
        # Type 0 makes an equal-depth transparent fragment resolve before the
        # opaque contact fragment, matching the depth test's boundary rule.
        hits.append((float(fragment["z"]), 0, fragment.get("id"), fragment))

    hits.sort(key=lambda hit: (hit[0], hit[1], material_key(hit[3]), hit[2]))

    first_opaque = None
    visible_transparent = []
    opaque_rejected = []
    transparent_rejected = []

    for _, fragment_type, fragment_id, fragment in hits:
        if first_opaque is not None:
            if fragment_type == 1:
                opaque_rejected.append(fragment_id)
            else:
                transparent_rejected.append(fragment_id)
        elif fragment_type == 1:
            first_opaque = fragment
        else:
            visible_transparent.append((fragment_id, fragment))

    visible_transparent.sort(
        key=lambda item: (
            -float(item[1]["z"]),
            material_key(item[1]),
            item[0],
        )
    )

    composite = composite_transparent(
        visible_transparent,
        None if first_opaque is None else first_opaque["albedo"],
        None if first_opaque is None else first_opaque.get("normal"),
        lights,
    )
    color_rgb = [
        sum(composite["final"][light_index][channel]
            for light_index in range(len(lights)))
        for channel in range(3)
    ]
    return {
        "color": color_rgb + [composite["alpha"]],
        "per_light": composite["final"],
        "occluded_opaque_indices": sorted(opaque_rejected),
        "occluded_transparent_indices": sorted(transparent_rejected),
    }


def clean_number(value):
    if value is None:
        return None
    value = round(float(value), 10)
    return 0.0 if value == 0.0 else value


def clean_deep(value):
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return clean_number(value)
    if isinstance(value, dict):
        return {key: clean_deep(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean_deep(item) for item in value]
    return value


def flatten_numbers(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        yield float(value)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from flatten_numbers(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from flatten_numbers(item)


def max_numeric_diff(left, right):
    left_values = list(flatten_numbers(left))
    right_values = list(flatten_numbers(right))
    if not left_values and not right_values:
        return 0.0
    return max(abs(a - b) for a, b in zip(left_values, right_values))


def render_signature(result):
    return clean_deep({key: result[key] for key in SIGNATURE_KEYS})


def digest_signature(signature):
    encoded = json.dumps(
        signature,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def make_submission_orders(count_transparent, run_count):
    base = tuple(range(count_transparent))
    candidates = [
        base,
        base[::-1],
        base[1:] + base[:1],
    ]
    candidates.extend(permutations(base))

    selected = []
    for order in candidates:
        if order not in selected:
            selected.append(order)
        if len(selected) >= run_count:
            break
    while len(selected) < run_count:
        selected.append(selected[0] if selected else tuple())
    return [list(order) for order in selected[:run_count]]


def run(req):
    opaque = tag_objects(req.get("opaque", []))
    transparent = tag_objects(req.get("trans", []))
    lights = req.get("lights", [])
    run_count = max(1, int(req.get("orders", 3)))
    orders = make_submission_orders(len(transparent), run_count)

    runs = []
    for order in orders:
        reordered_transparent = [transparent[index] for index in order]
        runs.append(render_once(opaque, reordered_transparent, lights))

    signatures = [render_signature(result) for result in runs]
    if any(signature != signatures[0] for signature in signatures[1:]):
        raise RuntimeError("transparent submission order changed the composite")
    digests = [digest_signature(signature) for signature in signatures]

    result = runs[0]
    result["digest"] = digests[0]

    reference = ray_reference(opaque, transparent, lights)
    if sorted(result["occluded_opaque_indices"]) != reference["occluded_opaque_indices"]:
        raise RuntimeError("opaque occlusion does not match independent ray reference")
    if (
        sorted(result["occluded_transparent_indices"])
        != reference["occluded_transparent_indices"]
    ):
        raise RuntimeError("transparent occlusion does not match independent ray reference")

    color_error = max_numeric_diff(result["color"], reference["color"])
    light_error = max_numeric_diff(result["per_light"], reference["per_light"])
    max_error = max(color_error, light_error)
    if max_error > ACCEPTANCE_ERROR:
        raise RuntimeError(
            "pipeline/ray reference color error "
            + str(max_error)
            + " > "
            + str(ACCEPTANCE_ERROR)
        )

    max_rgba_range = 0.0
    max_light_range = 0.0
    for current in runs[1:]:
        max_rgba_range = max(
            max_rgba_range, max_numeric_diff(current["color"], runs[0]["color"])
        )
        max_light_range = max(
            max_light_range,
            max_numeric_diff(current["per_light"], runs[0]["per_light"]),
        )

    result["ray_reference"] = {
        "color": reference["color"],
        "per_light": reference["per_light"],
    }
    result["validation"] = {
        "max_abs_error": max_error,
        "tolerance": ACCEPTANCE_ERROR,
        "passed": max_error <= ACCEPTANCE_ERROR,
        "occlusion_matches_reference": True,
    }
    result["order_check"] = {
        "requested_runs": run_count,
        "orders": orders,
        "digests": digests,
        "consistent": True,
        "max_rgba_range": max_rgba_range,
        "max_per_light_range": max_light_range,
    }
    result["color_space"] = "linear"
    result["lighting_model"] = (
        "lambert: max(0, dot(normal, normalize(light.dir))) * base * light.color"
    )
    return clean_deep(result)


def main():
    if len(sys.argv) != 3:
        raise SystemExit("usage: " + sys.argv[0] + " req.json output.json")
    with open(sys.argv[1], "r", encoding="utf-8") as source:
        request = json.load(source)
    output = run(request)
    with open(sys.argv[2], "w", encoding="utf-8") as destination:
        json.dump(output, destination, indent=2, ensure_ascii=False)
        destination.write("\n")


if __name__ == "__main__":
    main()
