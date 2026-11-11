"""Minimal Wavefront OBJ loader (standard library only).

Supported geometry records:

* ``v x y z [w]``   -- vertex positions (``w`` is ignored)
* ``vt u [v [w]]``  -- texture coordinates (only ``u``/``v`` are kept)
* ``f ...``         -- polygon faces, triangulated with a fan

Face references may be written as ``v``, ``v/vt``, ``v//vn`` or
``v/vt/vn``; normal indices are parsed but ignored.  Negative (relative)
indices are resolved against the records seen so far.

``load_mesh`` returns positions, UVs and indexed faces; ``load_obj`` keeps
the original position-only triangle list for backwards compatibility.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Tuple

Vertex = Tuple[float, float, float]
UV = Tuple[float, float]
Triangle = Tuple[Vertex, Vertex, Vertex]

# A face is ((v0, v1, v2), (t0, t1, t2)): position indices and texture
# indices (-1 when the face corner has no UV).
Face = Tuple[Tuple[int, int, int], Tuple[int, int, int]]


@dataclass
class Mesh:
    positions: List[Vertex]
    uvs: List[UV]
    faces: List[Face]

    def triangles(self) -> List[Triangle]:
        """Position-only triangle list (legacy view of the mesh)."""
        return [
            (self.positions[v[0]], self.positions[v[1]], self.positions[v[2]])
            for v, _ in self.faces
        ]


def load_mesh(path: str) -> Mesh:
    """Load *path* and return a :class:`Mesh` with positions, UVs and faces."""
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        lines = list(_join_continuations(handle))
    return parse_mesh(lines, source=path)


def load_obj(path: str) -> List[Triangle]:
    """Load *path* and return a list of position-only triangles."""
    return load_mesh(path).triangles()


def parse_obj(lines, source: str = "<obj>") -> List[Triangle]:
    """Parse an iterable of OBJ lines into position-only triangles."""
    return parse_mesh(lines, source=source).triangles()


def parse_mesh(lines, source: str = "<obj>") -> Mesh:
    """Parse an iterable of OBJ lines into a :class:`Mesh`."""
    positions: List[Vertex] = []
    uvs: List[UV] = []
    faces: List[Face] = []

    def resolve(token: str, count: int, what: str, lineno: int, raw: str) -> int:
        try:
            index = int(token)
        except ValueError as exc:
            raise ValueError(
                "%s:%d: invalid %s index %r (%s)"
                % (source, lineno, what, token, exc)
            ) from exc
        if index < 0:
            index = count + index
        elif index > 0:
            index -= 1
        if index < 0 or index >= count:
            raise ValueError(
                "%s:%d: %s index %s out of range (%d defined) in %r"
                % (source, lineno, what, token, count, raw)
            )
        return index

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
            positions.append(vertex)

        elif tag == "vt":
            if len(parts) < 3:
                raise ValueError(
                    "%s:%d: texture coordinate needs at least 2 components"
                    % (source, lineno)
                )
            try:
                uvs.append((float(parts[1]), float(parts[2])))
            except ValueError as exc:
                raise ValueError(
                    "%s:%d: invalid texture coordinate (%s)" % (source, lineno, exc)
                ) from exc

        elif tag == "f":
            if len(parts) < 4:
                raise ValueError(
                    "%s:%d: face needs at least 3 vertices" % (source, lineno)
                )
            v_idx = []
            t_idx = []
            for ref in parts[1:]:
                tokens = ref.split("/")
                if not tokens[0]:
                    raise ValueError(
                        "%s:%d: invalid face reference %r" % (source, lineno, ref)
                    )
                v_idx.append(resolve(tokens[0], len(positions), "vertex", lineno, ref))
                if len(tokens) > 1 and tokens[1]:
                    t_idx.append(resolve(tokens[1], len(uvs), "texture", lineno, ref))
                else:
                    t_idx.append(-1)

            # Fan triangulation: (0, i, i+1) for i in 1..n-2.
            for i in range(1, len(v_idx) - 1):
                faces.append(
                    (
                        (v_idx[0], v_idx[i], v_idx[i + 1]),
                        (t_idx[0], t_idx[i], t_idx[i + 1]),
                    )
                )

        # All other records (o, g, s, vn, mtllib, usemtl, ...) are
        # irrelevant to a geometry-only renderer and are ignored.

    if not positions:
        raise ValueError("%s: no vertices found" % source)
    return Mesh(positions=positions, uvs=uvs, faces=faces)


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
