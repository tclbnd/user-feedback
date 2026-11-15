"""Dual-quaternion skinning for twist-safe wrist deformation.

``python3 skin.py input.json output.json``

Linear blend skinning averages the bone *matrices*; under large twists the
averaged matrix is no longer orthogonal, so the surface shrinks towards the
twist axis and the joint collapses into a candy-wrapper shape.  Here every
bone transform is converted to a unit dual quaternion, the quaternions are
sign-aligned along the shortest path, blended and renormalized.  The blended
dual quaternion always denotes a rigid transform, so the wrist cross-section
keeps its circumference instead of losing volume.
"""

import hashlib
import json
import math
import sys
from pathlib import Path

# Normalized weights are snapped to the 2**-53 grid (integers over 2**53) so
# their exact sum is 1 and ``math.fsum`` of them is exactly 1.0 -- the reported
# weight-sum error is bit-exactly zero for every vertex.
_QUANT = 1 << 53


def transform(position, bone):
    w, x, y, z = bone["rotation"]
    px, py, pz = position
    cx, cy, cz = (y * pz - z * py, z * px - x * pz, x * py - y * px)
    dx, dy, dz = (y * cz - z * cy, z * cx - x * cz, x * cy - y * cx)
    return [value + 2 * (w * c + d) + shift for value, c, d, shift in
            zip(position, (cx, cy, cz), (dx, dy, dz), bone["translation"])]


def _unit_quaternion(rotation):
    """Normalize a rotation quaternion, mapping degenerate input to identity."""
    w, x, y, z = (float(value) for value in rotation)
    norm = math.sqrt(math.fsum((w * w, x * x, y * y, z * z)))
    if not math.isfinite(norm) or norm == 0.0:
        return (1.0, 0.0, 0.0, 0.0)
    if norm == 1.0:
        return (w, x, y, z)
    return (w / norm, x / norm, y / norm, z / norm)


def _dual_part(rotation, translation):
    """Dual part ``0.5 * (0, translation) * rotation`` of a rigid transform."""
    qw, qx, qy, qz = rotation
    tx, ty, tz = (float(value) for value in translation)
    dw = -(tx * qx + ty * qy + tz * qz)
    dx = qw * tx + (ty * qz - tz * qy)
    dy = qw * ty + (tz * qx - tx * qz)
    dz = qw * tz + (tx * qy - ty * qx)
    return (0.5 * dw, 0.5 * dx, 0.5 * dy, 0.5 * dz)


def _safe_fsum(values):
    """``math.fsum`` that degrades to infinity instead of overflowing."""
    try:
        return math.fsum(values)
    except OverflowError:
        return math.inf


def _vertex_weights(vertex):
    """Canonicalize a vertex' weights: merge, drop non-positive, sort.

    Sorting by (descending weight, bone id) makes everything downstream
    independent of the order of the bone table and of the weight list.
    """
    merged = {}
    for entry in vertex.get("weights") or []:
        weight = float(entry["weight"])
        if not weight > 0.0 or not math.isfinite(weight):
            # Zero, negative, NaN and infinite weights carry no usable
            # information; dropping them keeps the blend finite.
            continue
        merged.setdefault(entry["bone"], []).append(weight)
    combined = {bone_id: _safe_fsum(group)
                for bone_id, group in merged.items()}
    pairs = [(weight, bone_id) for bone_id, weight in combined.items()
             if weight > 0.0]
    pairs.sort(key=lambda pair: (-pair[0], pair[1]))
    return pairs


def _grid_weights(ratios, total):
    """Split ``2**53`` integer ticks proportionally (largest remainder)."""
    counts = []
    remainders = []
    for ratio in ratios:
        value = ratio / total * _QUANT
        base = int(math.floor(value))
        if base < 0:
            base = 0
        elif base > _QUANT:
            base = _QUANT
        counts.append(base)
        remainders.append(value - base)
    order = sorted(range(len(counts)), key=lambda index: (-remainders[index],
                                                          index))
    delta = _QUANT - sum(counts)
    step = 0
    while delta > 0:
        counts[order[step % len(counts)]] += 1
        delta -= 1
        step += 1
    while delta < 0:
        # Only reachable through floating-point rounding; take one tick back
        # from a strictly positive entry so weights never turn negative.
        donor = next((index for index in order if counts[index] > 0), None)
        if donor is None:
            break
        counts[donor] -= 1
        delta += 1
    return [count / _QUANT for count in counts]


def _normalized_weights(weights):
    """Normalized weights whose floating-point sum is exactly ``1.0``.

    Weights are first scaled by their maximum so that neither the sum nor the
    ratios can overflow.  Whenever the direct division already sums to one it
    is used verbatim; otherwise the weights are snapped to the ``2**-53`` grid,
    which always sums to exactly one and perturbs each weight by at most
    ``2**-53``.
    """
    count = len(weights)
    scale = max(weights)
    if not math.isfinite(scale) or scale <= 0.0:
        return [1.0 / count] * count
    ratios = [weight / scale for weight in weights]
    total = _safe_fsum(ratios)
    if total > 0.0 and math.isfinite(total):
        direct = [ratio / total for ratio in ratios]
        if math.fsum(direct) == 1.0:
            return direct
        return _grid_weights(ratios, total)
    return [1.0 / count] * count


def _sign_aligned(rotation, anchor):
    """Shortest-path hemisphere: align ``rotation`` with ``anchor``.

    ``rotation`` and ``-rotation`` describe the same rotation, so the
    hemisphere closest to the anchor is chosen.  For an exactly orthogonal pair
    there is no shorter path; then the representation whose first non-zero
    component is positive wins, which reproduces the reference convention.
    """
    dot = math.fsum(a * b for a, b in zip(rotation, anchor))
    if dot < 0.0:
        return -1.0
    if dot > 0.0:
        return 1.0
    for component in rotation:
        if component != 0.0:
            return -1.0 if component < 0.0 else 1.0
    return 1.0


def _blend_dual_quaternions(entries):
    """Blend canonical ``(weight, rotation, dual)`` entries into a rigid map.

    The first entry is the sign anchor (heaviest bone, ties broken by bone id).
    Returns ``(rotation, translation)`` or ``None`` if the blend degenerates.
    """
    anchor = entries[0][1]
    rotations = []
    duals = []
    for _, rotation, dual in entries:
        sign = _sign_aligned(rotation, anchor)
        if sign < 0.0:
            rotation = tuple(-component for component in rotation)
            dual = tuple(-component for component in dual)
        rotations.append(rotation)
        duals.append(dual)

    weights = [entry[0] for entry in entries]
    q = [math.fsum(weight * rotation[k]
                   for weight, rotation in zip(weights, rotations))
         for k in range(4)]
    d = [math.fsum(weight * dual[k] for weight, dual in zip(weights, duals))
         for k in range(4)]
    norm = math.sqrt(math.fsum(component * component for component in q))
    if norm == 0.0 or not math.isfinite(norm):
        return None
    q = tuple(component / norm for component in q)
    d = tuple(component / norm for component in d)
    # translation = 2 * vector(d * conj(q))
    tx = 2.0 * (q[0] * d[1] - d[0] * q[1] + q[2] * d[3] - q[3] * d[2])
    ty = 2.0 * (q[0] * d[2] - d[0] * q[2] + q[3] * d[1] - q[1] * d[3])
    tz = 2.0 * (q[0] * d[3] - d[0] * q[3] + q[1] * d[2] - q[2] * d[1])
    return q, (tx, ty, tz)


def _linear_blend(position, weighted, bones):
    """Linear blend fallback, only for a degenerate dual-quaternion blend."""
    blended = [0.0, 0.0, 0.0]
    for weight, (_, bone_id) in weighted:
        p = transform(position, bones[bone_id]["bone"])
        for axis in range(3):
            blended[axis] += p[axis] * weight
    return blended


def _rigid_position(position, rotation, translation):
    """Apply a blended rigid transform, keeping the identity bit-exact.

    At rest every bone is the identity transform, so the blend is the identity
    dual quaternion and the bind position is returned untouched -- not merely
    within rounding error, but exactly the input values.
    """
    if (abs(rotation[0]) == 1.0 and rotation[1] == 0.0 and rotation[2] == 0.0
            and rotation[3] == 0.0
            and translation == (0.0, 0.0, 0.0)):
        return list(position)
    return transform(position, {"rotation": rotation,
                                "translation": translation})


def skin(data):
    bones = {}
    for bone in data["bones"]:
        rotation = _unit_quaternion(bone["rotation"])
        bones[bone["id"]] = {
            "bone": bone,
            "rotation": rotation,
            "dual": _dual_part(rotation, bone["translation"]),
        }

    source_vertices = data.get("vertices") or []
    vertices = []
    errors = []
    counts = {"bind": 0, "dual_quaternion": 0, "linear": 0}
    single_bone = 0

    for vertex in source_vertices:
        weighted = _vertex_weights(vertex)
        if not weighted:
            # Zero total weight: there is no transform to apply, so the vertex
            # stays exactly at its bind position (same as the linear baseline).
            vertices.append({"position": list(vertex["position"]),
                             "method": "bind"})
            counts["bind"] += 1
            errors.append(0.0)
            continue

        normalized = _normalized_weights([weight for weight, _ in weighted])
        errors.append(abs(math.fsum(normalized) - 1.0))
        if len(weighted) == 1:
            # Single-bone degenerate vertex.  Its dual-quaternion blend is
            # exactly that one bone's transform, so apply the bone directly:
            # the vertex then matches linear blend skinning bit for bit, both
            # at rest and under any pose.
            single_bone += 1
            bone = bones[weighted[0][1]]["bone"]
            vertices.append({"position": _rigid_position(
                vertex["position"], bones[weighted[0][1]]["rotation"],
                tuple(float(v) for v in bone["translation"])),
                "method": "dual_quaternion"})
            counts["dual_quaternion"] += 1
            continue

        entries = []
        for weight, (_, bone_id) in zip(normalized, weighted):
            record = bones[bone_id]
            entries.append((weight, record["rotation"], record["dual"]))

        blended = _blend_dual_quaternions(entries)
        if blended is None:
            # Opposing twists cancel the rotation entirely; fall back to the
            # linear blend so the vertex still lands somewhere defined.
            position = _linear_blend(vertex["position"],
                                     list(zip(normalized, weighted)), bones)
            method = "linear"
        else:
            rotation, translation = blended
            position = _rigid_position(vertex["position"], rotation,
                                       translation)
            method = "dual_quaternion"
        vertices.append({"position": position, "method": method})
        counts[method] += 1

    positions = [vertex["position"] for vertex in vertices]
    digest = hashlib.sha256(json.dumps(positions).encode()).hexdigest()
    displacement = [
        math.sqrt(math.fsum((position[axis] - float(bind[axis])) ** 2
                            for axis in range(3)))
        for position, bind in zip(positions,
                                  (vertex["position"]
                                   for vertex in source_vertices))]

    return dict(
        vertices=vertices,
        stats={
            "max_weight_sum_error": max(errors, default=0.0),
            "method_counts": counts,
            "bind_vertices": counts["bind"],
            "degenerate_single_bone": single_bone,
            "position_min": [min(p[axis] for p in positions)
                             for axis in range(3)] if positions else [],
            "position_max": [max(p[axis] for p in positions)
                             for axis in range(3)] if positions else [],
            "position_centroid": [math.fsum(p[axis] for p in positions)
                                  / len(positions)
                                  for axis in range(3)] if positions else [],
            "max_displacement": max(displacement, default=0.0),
        },
        digest=digest,
    )


if __name__ == "__main__":
    data = json.loads(Path(sys.argv[1]).read_text())
    Path(sys.argv[2]).write_text(json.dumps(skin(data)))
