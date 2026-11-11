"""Software 3D pipeline: MVP, z-buffer, shadow maps, Phong shading.

Single directional light with an orthographic depth-only shadow pass.
The main pass performs perspective-correct interpolation of UVs and
world positions, a z-buffer test, shadow-map comparison and Phong
(ambient + diffuse + specular) shading against a bilinear texture.
"""

import math

import math3d


NEAR_EPS = 1e-4


class RenderMesh:
    """World-space mesh ready for rendering (already skinned if needed)."""

    def __init__(self, vertices, uvs, faces, texture=None,
                 kd=(1.0, 1.0, 1.0), specular=0.25, shininess=16.0):
        self.vertices = vertices      # [(x, y, z), ...]
        self.uvs = uvs                # [(u, v), ...]
        self.faces = faces            # [((vi, ui), (vi, ui), (vi, ui))]
        self.texture = texture
        self.kd = kd
        self.specular = specular
        self.shininess = shininess


class Renderer:
    def __init__(self, width, height, shadow_size=256):
        self.width = width
        self.height = height
        self.shadow_size = shadow_size

        # Main camera
        self.eye = (5.5, 4.2, 8.5)
        self.target = (0.0, 1.6, 0.0)
        self.up = (0.0, 1.0, 0.0)
        self.fovy = math.radians(50.0)
        self.near = 0.1
        self.far = 60.0
        self.view = math3d.look_at(self.eye, self.target, self.up)
        self.proj = math3d.perspective(
            self.fovy, width / height, self.near, self.far)
        self.vp = math3d.mul(self.proj, self.view)

        # Directional light (travel direction, normalized)
        self.light_dir = math3d.normalize((-0.45, -1.0, -0.35))
        light_target = (0.0, 1.5, 0.0)
        light_eye = (
            light_target[0] - self.light_dir[0] * 14.0,
            light_target[1] - self.light_dir[1] * 14.0,
            light_target[2] - self.light_dir[2] * 14.0,
        )
        self.light_view = math3d.look_at(light_eye, light_target, self.up)
        self.light_proj = math3d.ortho(-7.0, 7.0, -7.0, 7.0, 1.0, 30.0)
        self.light_vp = math3d.mul(self.light_proj, self.light_view)

        # Phong coefficients
        self.ambient = 0.25
        self.diffuse = 0.8
        self.light_color = (1.0, 1.0, 0.95)
        self.shadow_bias = 0.0035
        self.shadow_attenuation = 0.30   # ambient-only multiplier when shaded
        self.background = (24, 28, 40)

        # Frame buffers
        self.frame = bytearray(width * height * 3)
        self.zbuf = [1.0] * (width * height)
        self.shadow_z = [1.0] * (shadow_size * shadow_size)

    # ------------------------------------------------------------------
    # Triangle preparation
    # ------------------------------------------------------------------
    def _clip_near(self, verts):
        """Sutherland-Hodgman clip against the w > NEAR_EPS plane."""
        out = []
        count = len(verts)
        for i in range(count):
            a = verts[i]
            b = verts[(i + 1) % count]
            a_in = a[3] > NEAR_EPS
            b_in = b[3] > NEAR_EPS
            if a_in:
                out.append(a)
            if a_in != b_in:
                denom = a[3] - b[3]
                t = (a[3] - NEAR_EPS) / denom if denom != 0.0 else 0.0
                out.append(tuple(a[k] + t * (b[k] - a[k])
                                 for k in range(len(a))))
        return out

    def _build_triangles(self, mesh, mvp, w_pos_attrs=True):
        """Clip + project every face; yields clipped vertex tuples.

        Vertex tuple layout:
        (cx, cy, cz, cw, wx, wy, wz, u, v)
        """
        triangles = []
        transform = math3d.clip_transform
        for face in mesh.faces:
            raw = []
            for vi, ui in face:
                world = mesh.vertices[vi]
                clip = transform(mvp, world)
                if ui is not None and ui < len(mesh.uvs):
                    u, v = mesh.uvs[ui]
                else:
                    u, v = 0.0, 0.0
                raw.append((clip[0], clip[1], clip[2], clip[3],
                            world[0], world[1], world[2], u, v))
            clipped = self._clip_near(raw)
            for k in range(1, len(clipped) - 1):
                triangles.append((clipped[0], clipped[k], clipped[k + 1]))
        return triangles

    # ------------------------------------------------------------------
    # Shadow (depth-only) pass
    # ------------------------------------------------------------------
    def render_shadow(self, meshes):
        size = self.shadow_size
        zbuf = self.shadow_z
        for i in range(size * size):
            zbuf[i] = 1.0
        half = size * 0.5

        for mesh in meshes:
            for tri in self._build_triangles(mesh, self.light_vp):
                pts = []
                for vtx in tri:
                    cw = vtx[3]
                    if cw <= NEAR_EPS:
                        break
                    sx = (vtx[0] / cw * 0.5 + 0.5) * size
                    sy = (1.0 - (vtx[1] / cw * 0.5 + 0.5)) * size
                    pts.append((sx, sy, vtx[2] / cw))
                else:
                    self._rasterize_depth(pts, zbuf, size, half)

    @staticmethod
    def _rasterize_depth(pts, zbuf, size, half):
        (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = pts
        denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(denom) < 1e-9:
            return
        min_x = max(int(min(x1, x2, x3)), 0)
        max_x = min(int(max(x1, x2, x3)) + 1, size - 1)
        min_y = max(int(min(y1, y2, y3)), 0)
        max_y = min(int(max(y1, y2, y3)) + 1, size - 1)
        if min_x > max_x or min_y > max_y:
            return
        positive = denom > 0.0
        for py in range(min_y, max_y + 1):
            px = min_x
            row_base = py * size
            while px <= max_x:
                l1 = (y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)
                l2 = (y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)
                l3 = denom - l1 - l2
                if positive:
                    if l1 >= 0.0 and l2 >= 0.0 and l3 >= 0.0:
                        z = (z1 * l1 + z2 * l2 + z3 * l3) / denom
                        idx = row_base + px
                        if z < zbuf[idx]:
                            zbuf[idx] = z
                elif l1 <= 0.0 and l2 <= 0.0 and l3 <= 0.0:
                    z = (z1 * l1 + z2 * l2 + z3 * l3) / denom
                    idx = row_base + px
                    if z < zbuf[idx]:
                        zbuf[idx] = z
                px += 1

    # ------------------------------------------------------------------
    # Main shaded pass
    # ------------------------------------------------------------------
    def render_frame(self, meshes):
        width, height = self.width, self.height
        frame, zbuf = self.frame, self.zbuf
        for i in range(width * height):
            zbuf[i] = 1.0
        br, bg, bb = self.background
        for i in range(0, len(frame), 3):
            frame[i] = br
            frame[i + 1] = bg
            frame[i + 2] = bb

        stats = {'tris_drawn': 0, 'fragments': 0, 'shadowed': 0}
        light_vp = self.light_vp
        shadow_z = self.shadow_z
        shadow_size = self.shadow_size
        bias = self.shadow_bias
        eye = self.eye
        l_dir = self.light_dir
        # vector pointing toward the light (for diffuse term)
        to_light = (-l_dir[0], -l_dir[1], -l_dir[2])

        for mesh in meshes:
            texture = mesh.texture
            kd = mesh.kd
            specular = mesh.specular
            shininess = mesh.shininess
            triangles = self._build_triangles(mesh, self.vp)
            stats['tris_drawn'] += len(triangles)

            for tri in triangles:
                # Flat-shaded face normal from world positions.
                w1 = (tri[0][4], tri[0][5], tri[0][6])
                w2 = (tri[1][4], tri[1][5], tri[1][6])
                w3 = (tri[2][4], tri[2][5], tri[2][6])
                e1 = math3d.sub(w2, w1)
                e2 = math3d.sub(w3, w1)
                normal = math3d.normalize(math3d.cross(e1, e2))
                if normal == (0.0, 0.0, 0.0):
                    continue

                # Screen setup with perspective-correct attribute vectors.
                screen = []
                for vtx in tri:
                    cw = vtx[3]
                    inv_w = 1.0 / cw
                    sx = (vtx[0] * inv_w * 0.5 + 0.5) * width
                    sy = (1.0 - (vtx[1] * inv_w * 0.5 + 0.5)) * height
                    screen.append((
                        sx, sy, vtx[2] * inv_w, inv_w,
                        vtx[7] * inv_w, vtx[8] * inv_w,   # u/w, v/w
                        vtx[4] * inv_w, vtx[5] * inv_w, vtx[6] * inv_w,
                    ))
                self._rasterize_shaded(
                    screen, normal, texture, kd, specular, shininess,
                    frame, zbuf, stats, light_vp, shadow_z, shadow_size,
                    bias, eye, to_light, width, height)

        stats['shadowed_pct'] = (
            100.0 * stats['shadowed'] / stats['fragments']
            if stats['fragments'] else 0.0)
        return bytes(frame), stats

    def _rasterize_shaded(self, screen, normal, texture, kd, specular,
                          shininess, frame, zbuf, stats, light_vp,
                          shadow_z, shadow_size, bias, eye, to_light,
                          width, height):
        (x1, y1, z1, iw1, u1, v1, wx1, wy1, wz1), \
            (x2, y2, z2, iw2, u2, v2, wx2, wy2, wz2), \
            (x3, y3, z3, iw3, u3, v3, wx3, wy3, wz3) = screen

        denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
        if abs(denom) < 1e-9:
            return
        min_x = max(int(min(x1, x2, x3)), 0)
        max_x = min(int(max(x1, x2, x3)) + 1, width - 1)
        min_y = max(int(min(y1, y2, y3)), 0)
        max_y = min(int(max(y1, y2, y3)) + 1, height - 1)
        if min_x > max_x or min_y > max_y:
            return
        positive = denom > 0.0

        # Two-sided lighting: flip normal if it faces away from the camera.
        face_center = (
            (wx1 / iw1 + wx2 / iw2 + wx3 / iw3) / 3.0,
            (wy1 / iw1 + wy2 / iw2 + wy3 / iw3) / 3.0,
            (wz1 / iw1 + wz2 / iw2 + wz3 / iw3) / 3.0)
        view_vec = math3d.sub(eye, face_center)
        if math3d.dot(normal, view_vec) < 0.0:
            normal = (-normal[0], -normal[1], -normal[2])

        n_dot_l = math3d.dot(normal, to_light)
        if n_dot_l < 0.0:
            n_dot_l = 0.0
        view_dir = math3d.normalize(view_vec)
        half_vec = math3d.normalize(math3d.add(to_light, view_dir))
        n_dot_h = math3d.dot(normal, half_vec)
        if n_dot_h < 0.0:
            n_dot_h = 0.0
        spec_term = specular * (n_dot_h ** shininess)

        ambient = self.ambient
        diffuse = self.diffuse * n_dot_l
        lc = self.light_color
        atten = self.shadow_attenuation
        inv_gamma = 1.0 / 2.2

        for py in range(min_y, max_y + 1):
            row_base = py * width
            px = min_x
            while px <= max_x:
                l1 = (y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)
                l2 = (y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)
                l3 = denom - l1 - l2
                inside = (l1 >= 0.0 and l2 >= 0.0 and l3 >= 0.0) if positive \
                    else (l1 <= 0.0 and l2 <= 0.0 and l3 <= 0.0)
                if inside:
                    inv_denom = 1.0 / denom
                    b1 = l1 * inv_denom
                    b2 = l2 * inv_denom
                    b3 = l3 * inv_denom
                    z = z1 * b1 + z2 * b2 + z3 * b3
                    idx = row_base + px
                    if z < zbuf[idx]:
                        zbuf[idx] = z
                        stats['fragments'] += 1

                        inv_w = iw1 * b1 + iw2 * b2 + iw3 * b3
                        w_recip = 1.0 / inv_w
                        u = (u1 * b1 + u2 * b2 + u3 * b3) * w_recip
                        v = (v1 * b1 + v2 * b2 + v3 * b3) * w_recip
                        wx = (wx1 * b1 + wx2 * b2 + wx3 * b3) * w_recip
                        wy = (wy1 * b1 + wy2 * b2 + wy3 * b3) * w_recip
                        wz = (wz1 * b1 + wz2 * b2 + wz3 * b3) * w_recip

                        # Shadow map lookup (orthographic light).
                        lclip = math3d.clip_transform(
                            light_vp, (wx, wy, wz))
                        in_shadow = False
                        lcw = lclip[3]
                        if lcw > 0.0:
                            ndc_x = lclip[0] / lcw * 0.5 + 0.5
                            ndc_y = lclip[1] / lcw * 0.5 + 0.5
                            ndc_z = lclip[2] / lcw
                            if 0.0 <= ndc_x < 1.0 and 0.0 <= ndc_y < 1.0 \
                                    and 0.0 <= ndc_z <= 1.0:
                                tx = int(ndc_x * shadow_size)
                                ty = int((1.0 - ndc_y) * shadow_size)
                                if ty >= shadow_size:
                                    ty = shadow_size - 1
                                if shadow_z[ty * shadow_size + tx] + bias \
                                        < ndc_z:
                                    in_shadow = True
                        if in_shadow:
                            stats['shadowed'] += 1

                        # Texture / base color.
                        if texture is not None:
                            tr, tg, tb = texture.sample(u, v)
                        else:
                            tr, tg, tb = 255.0, 255.0, 255.0
                        base_r = tr * kd[0]
                        base_g = tg * kd[1]
                        base_b = tb * kd[2]

                        if in_shadow:
                            cr = base_r * ambient * atten
                            cg = base_g * ambient * atten
                            cb = base_b * ambient * atten
                        else:
                            cr = base_r * (ambient + diffuse * lc[0]) \
                                + 255.0 * spec_term * lc[0]
                            cg = base_g * (ambient + diffuse * lc[1]) \
                                + 255.0 * spec_term * lc[1]
                            cb = base_b * (ambient + diffuse * lc[2]) \
                                + 255.0 * spec_term * lc[2]

                        # Gamma encode and store.
                        frame[idx * 3] = min(
                            255, int(255.0 * ((cr / 255.0) ** inv_gamma)))
                        frame[idx * 3 + 1] = min(
                            255, int(255.0 * ((cg / 255.0) ** inv_gamma)))
                        frame[idx * 3 + 2] = min(
                            255, int(255.0 * ((cb / 255.0) ** inv_gamma)))
                px += 1
