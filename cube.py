"""Directional lookup and roughness prefiltering of a six-face cubemap.

Conventions (see README.md):
  * faces are ordered +x,-x,+y,-y,+z,-z; row index is v, column index is u.
  * texel i center coordinate is c(i,N) = 2*(i+0.5)/N - 1, in [-1, 1).
  * locate() is the inverse of direction(); ties on |axis| follow x,y,z.

Bilinear filtering treats the cubemap as one physical sphere: a neighbour
that leaves a face across an edge or corner is mapped through its 3-D
direction onto the neighbour face instead of being clamped on the original
face, which removes cube-edge seams.

Prefiltering for roughness r>0 integrates the kernel

    w(n) = max(0, d . n) ** k,   k = max(0.0, 2/r**2 - 2.0)

(k == 0 means a constant kernel over the positive hemisphere). Every source
texel is weighted by its exact projected solid angle
max(0, d . n) * solid_angle(texel), so constant environments stay exactly
constant and the total kernel energy is normalized away. The integration is
deterministic; an optional "seed" is accepted for the documented schema but
does not change the result, which makes every roughness level reproducible.
"""
import argparse
import json
import math
from pathlib import Path

FACES = ("+x", "-x", "+y", "-y", "+z", "-z")


def direction(face, u, v):
    """3-D direction for face-local (u, v) coordinates in [-1, 1]."""
    return {
        "+x": (1.0, -v, -u),
        "-x": (-1.0, -v, u),
        "+y": (u, 1.0, v),
        "-y": (u, -1.0, -v),
        "+z": (u, -v, 1.0),
        "-z": (-u, -v, -1.0),
    }[face]


def locate(d):
    """Locate direction d (any non-zero scale) on a face.

    Returns (face, u, v). On |axis| ties x beats y beats z, matching
    direction().
    """
    x, y, z = d
    if abs(x) >= max(abs(y), abs(z)):
        if x > 0:
            return ("+x", -z / x, -y / x)
        return ("-x", z / -x, y / x)
    if abs(y) >= abs(z):
        if y > 0:
            return ("+y", x / y, z / y)
        return ("-y", x / -y, z / y)
    if z > 0:
        return ("+z", x / z, -y / z)
    return ("-z", x / z, y / z)


def _norm(x, y, z):
    length = math.sqrt(x * x + y * y + z * z)
    return (x / length, y / length, z / length)


def _center(index, size):
    """Center coordinate of texel `index` in a face of side `size`."""
    return (2.0 * index + 1.0) / size - 1.0


def _nearest_index(t, size):
    idx = int(math.floor((t + 1.0) * 0.5 * size))
    if idx < 0:
        return 0
    if idx >= size:
        return size - 1
    return idx


def nearest_crossface(faces, face, u, v):
    """Nearest-texel value at face-local (u, v), crossing edges/corners.

    If (u, v) stays inside [-1, 1) the local texel is read. Otherwise the
    requested point is turned into a 3-D direction and re-located on the
    neighbour face (the same rule handles both edges and cube corners);
    nothing is clamped on the original face.
    """
    n = len(faces[face])
    if -1.0 <= u < 1.0 and -1.0 <= v < 1.0:
        return faces[face][_nearest_index(v, n)][_nearest_index(u, n)]
    dx, dy, dz = direction(face, u, v)
    nface, nu, nv = locate((dx, dy, dz))
    grid = faces[nface]
    side = len(grid)
    return grid[_nearest_index(nv, side)][_nearest_index(nu, side)]


def sample(faces, d):
    """Cross-face bilinear value for physical direction d."""
    face, u, v = locate(d)
    n = len(faces[face])
    # Local coordinates of the four surrounding texel centers.
    i0 = int(math.floor((u + 1.0) * 0.5 * n - 0.5))
    j0 = int(math.floor((v + 1.0) * 0.5 * n - 0.5))
    u0 = _center(i0, n)
    v0 = _center(j0, n)
    fu = (u - u0) * 0.5 * n
    fv = (v - v0) * 0.5 * n
    # Numerical safety; texel centers never coincide with a border exactly.
    fu = min(1.0, max(0.0, fu))
    fv = min(1.0, max(0.0, fv))

    def val(i, j):
        return nearest_crossface(faces, face, _center(i, n), _center(j, n))

    c00 = val(i0, j0)
    c10 = val(i0 + 1, j0)
    c01 = val(i0, j0 + 1)
    c11 = val(i0 + 1, j0 + 1)
    top = c00 * (1.0 - fu) + c10 * fu
    bottom = c01 * (1.0 - fu) + c11 * fu
    return top * (1.0 - fv) + bottom * fv


def texel_solid_angle(face, i, j, size):
    """Exact solid angle of cubemap texel (i, j).

    The texel spans axis-coordinate bounds [x0, x1] x [y0, y1] on the unit
    cube. For a rectangle on the plane w = +/-1 the solid angle is

        atan2(x1*y1, r1) - atan2(x0*y1, r01)
        - atan2(x1*y0, r10) + atan2(x0*y0, r0),

    with r = sqrt(1 + x^2 + y^2); signs of x, y are handled by atan2.
    """
    x0 = 2.0 * i / size - 1.0
    x1 = 2.0 * (i + 1) / size - 1.0
    y0 = 2.0 * j / size - 1.0
    y1 = 2.0 * (j + 1) / size - 1.0

    def term(x, y):
        return math.atan2(x * y, math.sqrt(1.0 + x * x + y * y))

    omega = term(x1, y1) - term(x0, y1) - term(x1, y0) + term(x0, y0)
    # The differential solid angle dOmega = dA / (1+x^2+y^2)^(3/2) is
    # positive on every cube plane, so no sign flip for negative faces.
    return omega


def build_source_table(faces):
    """Flatten the source cubemap into (unit_direction, radiance, omega)."""
    n = len(faces[FACES[0]])
    table = []
    for face in FACES:
        grid = faces[face]
        for j in range(n):
            v = _center(j, n)
            for i in range(n):
                u = _center(i, n)
                unit = _norm(*direction(face, u, v))
                omega = texel_solid_angle(face, i, j, n)
                table.append((unit, grid[j][i], omega))
    return table


def kernel_exponent(roughness):
    """k = max(0, 2/r^2 - 2); r == 0 is handled separately (bilinear)."""
    return max(0.0, 2.0 / (roughness * roughness) - 2.0)


def prefiltered_value(source_table, d, exponent):
    """Solid-angle weighted cosine-kernel integral in direction d."""
    dx, dy, dz = d
    weighted_sum = 0.0
    weight_sum = 0.0
    if exponent == 0.0:
        # Constant kernel over the positive hemisphere.
        for (nx, ny, nz), radiance, omega in source_table:
            cosine = dx * nx + dy * ny + dz * nz
            if cosine > 0.0:
                weight = cosine * omega
                weighted_sum += radiance * weight
                weight_sum += weight
    else:
        for (nx, ny, nz), radiance, omega in source_table:
            cosine = dx * nx + dy * ny + dz * nz
            if cosine > 0.0:
                weight = (cosine ** exponent) * cosine * omega
                weighted_sum += radiance * weight
                weight_sum += weight
    if weight_sum <= 0.0:
        return 0.0
    return weighted_sum / weight_sum


def build_level(source_table, faces, roughness, size):
    """One prefiltered cubemap level plus its face data.

    Level mapping is positional: roughness[i] pairs with sizes[i].
    r == 0 reuses cross-face bilinear filtering; r > 0 uses the normalized
    solid-angle weighted kernel integral.
    """
    if roughness == 0.0:
        level_faces = {
            face: [
                [
                    sample(faces, direction(face, _center(i, size), _center(j, size)))
                    for i in range(size)
                ]
                for j in range(size)
            ]
            for face in FACES
        }
        return {
            "roughness": roughness,
            "size": size,
            "faces": level_faces,
        }

    exponent = kernel_exponent(roughness)
    level_faces = {}
    for face in FACES:
        grid = []
        for j in range(size):
            row = []
            v = _center(j, size)
            for i in range(size):
                u = _center(i, size)
                d = _norm(*direction(face, u, v))
                row.append(prefiltered_value(source_table, d, exponent))
            grid.append(row)
        level_faces[face] = grid
    return {
        "roughness": roughness,
        "size": size,
        "faces": level_faces,
    }


def run(data):
    faces = data["faces"]
    directions = [_norm(*d) for d in data["directions"]]

    # r == 0 always uses cross-face bilinear lookup of the source.
    samples = [sample(faces, d) for d in directions]

    roughness_list = data["roughness"]
    sizes = data["sizes"]
    if len(roughness_list) != len(sizes):
        raise ValueError("roughness and sizes must have equal length")

    source_table = None
    levels = []
    for roughness, size in zip(roughness_list, sizes):
        roughness = float(roughness)
        size = int(size)
        if roughness > 0.0 and source_table is None:
            source_table = build_source_table(faces)
        level = build_level(source_table, faces, roughness, size)
        if roughness == 0.0:
            level["samples"] = list(samples)
        else:
            exponent = kernel_exponent(roughness)
            level["samples"] = [
                prefiltered_value(source_table, d, exponent) for d in directions
            ]
        levels.append(level)

    return {"samples": samples, "levels": levels}


def main():
    parser = argparse.ArgumentParser(
        description="Cross-face cubemap sampling and roughness prefiltering."
    )
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    data = json.loads(args.input.read_text())
    result = run(data)
    args.output.write_text(
        json.dumps(result, sort_keys=True, allow_nan=False) + "\n"
    )


if __name__ == "__main__":
    main()
