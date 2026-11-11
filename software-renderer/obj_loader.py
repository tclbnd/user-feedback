"""Minimal Wavefront OBJ loader (standard library only).

Only the geometry records needed by the software renderer are supported:

* ``v x y z [w]``  -- vertex positions (``w`` is ignored)
* ``f ...``        -- polygon faces, triangulated with a fan

Face references may be written as ``v``, ``v/vt``, ``v//vn`` or
``v/vt/vn``; texture/normal indices are parsed but ignored.  Negative
(relative) indices are resolved against the vertices seen so far.
"""

from __future__ import annotations

from typing import List, Tuple

Vertex = Tuple[float, float, float]
Triangle = Tuple[Vertex, Vertex, Vertex]


def load_obj(path: str) -> List[Triangle]:
    """Load *path* and return a list of triangles.

    Each triangle is a tuple of three ``(x, y, z)`` vertex tuples.  Polygon
    faces are fan-triangulated.  Counts and errors are reported with the
    source line number.
    """
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        lines = list(_join_continuations(handle))
    return parse_obj(lines, source=path)


def parse_obj(lines, source: str = "<obj>") -> List[Triangle]:
    """Parse an iterable of OBJ lines into triangles."""
    vertices: List[Vertex] = []
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

        elif tag == "f":
            if len(parts) < 4:
                raise ValueError(
                    "%s:%d: face needs at least 3 vertices" % (source, lineno)
                )
            indices = []
            for ref in parts[1:]:
                token = ref.split("/", 1)[0]
                if not token:
                    raise ValueError(
                        "%s:%d: invalid face reference %r" % (source, lineno, ref)
                    )
                try:
                    index = int(token)
                except ValueError as exc:
                    raise ValueError(
                        "%s:%d: invalid face index %r" % (source, lineno, token)
                    ) from exc
                if index < 0:
                    index = len(vertices) + index
                elif index > 0:
                    index -= 1
                if index < 0 or index >= len(vertices):
                    raise ValueError(
                        "%s:%d: face index %d out of range (%d vertices defined)"
                        % (source, lineno, int(token), len(vertices))
                    )
                indices.append(index)

            # Fan triangulation: (0, i, i+1) for i in 1..n-2.
            for i in range(1, len(indices) - 1):
                triangles.append(
                    (
                        vertices[indices[0]],
                        vertices[indices[i]],
                        vertices[indices[i + 1]],
                    )
                )

        # All other records (o, g, s, vt, vn, mtllib, usemtl, ...) are
        # irrelevant to a geometry-only renderer and are ignored.

    if not vertices:
        raise ValueError("%s: no vertices found" % source)
    return triangles


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
