"""OBJ + MTL loader (stdlib only).

Supports OBJ ``v`` / ``vt`` / ``f`` records and MTL ``Kd`` / ``map_Kd``
records.  Polygon faces are fan-triangulated.  Face refs may be of the
forms ``v``, ``v/vt``, ``v//vn`` or ``v/vt/vn``; negative indices are
resolved relative to the current counts.
"""

import os

from png_texture import Texture


class Material:
    def __init__(self, name):
        self.name = name
        self.kd = (1.0, 1.0, 1.0)      # diffuse color
        self.texture = None           # png_texture.Texture or None
        self.texture_path = None


class Mesh:
    def __init__(self, name):
        self.name = name
        self.vertices = []            # list of (x, y, z)
        self.uvs = []                 # list of (u, v)
        self.faces = []               # list of ((vi, ui), x3)
        self.material = None          # Material or None


def _parse_mtl(path):
    materials = {}
    current = None
    base_dir = os.path.dirname(path)
    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            parts = line.split()
            if not parts:
                continue
            tag = parts[0].lower()
            if tag == 'newmtl':
                current = Material(parts[1])
                materials[parts[1]] = current
            elif current is None:
                continue
            elif tag == 'kd' and len(parts) >= 4:
                current.kd = (float(parts[1]), float(parts[2]),
                              float(parts[3]))
            elif tag == 'map_kd':
                tex_path = parts[-1]
                current.texture_path = tex_path
                current.texture = Texture.from_file(
                    os.path.join(base_dir, tex_path))
    return materials


def _parse_face_ref(ref, n_verts, n_uvs):
    """Return (vertex_index, uv_index_or_None); indices are 0-based."""
    bits = ref.split('/')
    vi = int(bits[0])
    if vi < 0:
        vi = n_verts + vi
    else:
        vi -= 1
    ui = None
    if len(bits) >= 2 and bits[1]:
        ui = int(bits[1])
        if ui < 0:
            ui = n_uvs + ui
        else:
            ui -= 1
    return vi, ui


def load_obj(path):
    """Load a single-mesh OBJ file and return a Mesh instance."""
    base_dir = os.path.dirname(path)
    materials = {}
    mesh = Mesh(os.path.splitext(os.path.basename(path))[0])
    active_material = None

    with open(path, 'r', encoding='utf-8', errors='replace') as fh:
        for line in fh:
            parts = line.split()
            if not parts or line.startswith('#'):
                continue
            tag = parts[0].lower()

            if tag == 'mtllib':
                mtl_path = os.path.join(base_dir, parts[1])
                if os.path.exists(mtl_path):
                    materials.update(_parse_mtl(mtl_path))
            elif tag == 'o' or tag == 'g':
                if parts[1:]:
                    mesh.name = parts[1]
            elif tag == 'v' and len(parts) >= 4:
                mesh.vertices.append(
                    (float(parts[1]), float(parts[2]), float(parts[3])))
            elif tag == 'vt' and len(parts) >= 3:
                mesh.uvs.append((float(parts[1]), float(parts[2])))
            elif tag == 'usemtl':
                name = parts[1]
                active_material = materials.get(name)
                if active_material is None and mesh.material is None:
                    active_material = Material(name)
            elif tag == 'f' and len(parts) >= 4:
                refs = [_parse_face_ref(r, len(mesh.vertices),
                                        len(mesh.uvs)) for r in parts[1:]]
                # fan triangulation around the first vertex
                for k in range(1, len(refs) - 1):
                    mesh.faces.append((refs[0], refs[k], refs[k + 1]))

    mesh.material = active_material
    return mesh
