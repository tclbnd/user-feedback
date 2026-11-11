"""Minimal Wavefront OBJ loader (standard library only).

Only the geometry records needed by the software renderer are supported:

* ``v x y z [w]``  -- vertex positions (``w`` is ignored)
* ``vt u v [w]``   -- texture coordinates (``w`` is ignored)
* ``f ...``        -- polygon faces, triangulated with a fan or, for quads,
                     with the alternate diagonal

Face references may be written as ``v``, ``v/vt``, ``v//vn`` or
``v/vt/vn``; normal indices are parsed but ignored.  Negative (relative)
indices are resolved against the records seen so far.

Two APIs are provided:

* :func:`load_obj` returns legacy ``(p0, p1, p2)`` position triangles.
* :func:`load_obj_scene` returns :class:`geometry.Triangle` records which
  keep per-corner texture coordinates.
"""

from __future__ import annotations

from typing import Iterable, List, Optional, Sequence, Tuple

from geometry import Triangle, Vec2, Vec3

Vertex = Tuple[float, float, float]
LegacyTriangle = Tuple[Vertex, Vertex, Vertex]


def load_obj(path: str, triangulation: str = "fan") -> List[LegacyTriangle]:
    """Load *path* and return a list of position-only triangles.

    Each triangle is a tuple of three ``(x, y, z)`` vertex tuples.  Polygon
    faces are fan-triangulated.  Counts and errors are reported with the
    source line number.
    """
    return convert_to_legacy(_load(path, triangulation))


def load_obj_scene(path: str, triangulation: str = "fan") -> List[Triangle]:
    """Load *path* and return triangles that retain texture coordinates."""
    return _load(path, triangulation)


def _load(path: str, triangulation: str) -> List[Triangle]:
    _validate_triangulation(triangulation)
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        lines = list(_join_continuations(handle))
    return parse_obj_scene(lines, source=path, triangulation=triangulation)


def parse_obj(
    lines: Iterable[str],
    source: str = "<obj>",
    triangulation: str = "fan",
) -> List[LegacyTriangle]:
    """Parse an iterable of OBJ lines into position-only triangles."""
    return convert_to_legacy(
        parse_obj_scene(lines, source=source, triangulation=triangulation)
    )


def parse_obj_scene(
    lines: Iterable[str],
    source: str = "<obj>",
    triangulation: str = "fan",
) -> List[Triangle]:
    """Parse an iterable of OBJ lines into triangles with UVs."""
    _validate_triangulation(triangulation)

    vertices: List[Vec3] = []
    texcoords: List[Vec2] = []
    triangles: List[Triangle] = []

    for lineno, raw in enumerate(lines, 1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        parts = line.split()
        tag = parts[0]

        if tag == "v":
            if len(parts) < 4:
                raise ValueError(
                    "%s:%d: vertex needs at least 3 coordinates" % (source, lineno)
                )
            try:
                vertex = (float(parts[1]), float(parts[2]), float(parts[3]))
            except ValueError as exc:
                raise ValueError(
                    "%s:%d: invalid vertex coordinate (%s)" % (source, lineno, exc)
                ) from exc
            vertices.append(vertex)

        elif tag == "vt":
            if len(parts) < 3:
                raise ValueError(
                    "%s:%d: texture coordinate needs at least 2 values"
                    % (source, lineno)
                )
            try:
                texcoords.append((float(parts[1]), float(parts[2])))
            except ValueError as exc:
                raise ValueError(
                    "%s:%d: invalid texture coordinate (%s)"
                    % (source, lineno, exc)
                ) from exc

        elif tag == "f":
            if len(parts) < 4:
                raise ValueError(
                    "%s:%d: face needs at least 3 vertices" % (source, lineno)
                )
            position_indices: List[int] = []
            uv_indices: List[Optional[int]] = []
            for ref in parts[1:]:
                fields = ref.split("/")
                token = fields[0]
                if not token:
                    raise ValueError(
                        "%s:%d: invalid face reference %r" % (source, lineno, ref)
                    )
                position_indices.append(
                    _resolve_index(
                        token,
                        len(vertices),
                        source,
                        lineno,
                        "vertex",
                        raw_token=token,
                    )
                )

                uv_index: Optional[int] = None
                if len(fields) >= 2 and fields[1]:
                    uv_index = _resolve_index(
                        fields[1],
                        len(texcoords),
                        source,
                        lineno,
                        "texture coordinate",
                        raw_token=fields[1],
                    )
                uv_indices.append(uv_index)

            for a, b, c in _triangulate(len(position_indices), triangulation):
                corners = (position_indices[a], position_indices[b], position_indices[c])
                uv_corners = (uv_indices[a], uv_indices[b], uv_indices[c])
                if all(index is not None for index in uv_corners):
                    uvs = (
                        texcoords[uv_corners[0]],  # type: ignore[index]
                        texcoords[uv_corners[1]],  # type: ignore[index]
                        texcoords[uv_corners[2]],  # type: ignore[index]
                    )
                else:
                    uvs = (None, None, None)
                triangles.append(
                    Triangle(
                        vertices[corners[0]],
                        vertices[corners[1]],
                        vertices[corners[2]],
                        uvs[0],
                        uvs[1],
                        uvs[2],
                    )
                )

        # All other records (o, g, s, vn, mtllib, usemtl, ...) are
        # irrelevant to this geometry/texture renderer and are ignored.

    if not vertices:
        raise ValueError("%s: no vertices found" % source)
    return triangles


def _triangulate(count: int, triangulation: str) -> List[Tuple[int, int, int]]:
    if count < 3:
        return []
    if triangulation == "alternate" and count == 4:
        return [(0, 1, 3), (1, 2, 3)]
    return [(0, i, i + 1) for i in range(1, count - 1)]


def _resolve_index(
    token: str,
    count: int,
    source: str,
    lineno: int,
    kind: str,
    raw_token: str = "",
) -> int:
    try:
        index = int(token)
    except ValueError as exc:
        raise ValueError(
            "%s:%d: invalid %s index %r" % (source, lineno, kind, token)
        ) from exc
    if index < 0:
        index = count + index
    elif index > 0:
        index -= 1
    if index < 0 or index >= count:
        raise ValueError(
            "%s:%d: %s index %s out of range (%d defined)"
            % (source, lineno, kind, raw_token or token, count)
        )
    return index


def _validate_triangulation(triangulation: str) -> None:
    if triangulation not in ("fan", "alternate"):
        raise ValueError("triangulation must be 'fan' or 'alternate'")


def convert_to_legacy(triangles: Sequence[Triangle]) -> List[LegacyTriangle]:
    return [(triangle.p0, triangle.p1, triangle.p2) for triangle in triangles]


def _join_continuations(handle):
    """Yield logical lines, joining backslash continuations."""
    pending = ""
    for raw in handle:
        stripped = raw.rstrip("\r\n")
        if stripped.endswith("\\"):
            pending += stripped[:-1] + " "
        else:
            yield pending + stripped
            pending = ""
    if pending:
        yield pending
