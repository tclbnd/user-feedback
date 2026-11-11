"""Software 3D pipeline: MVP, z-buffer, cascaded shadow maps, Phong shading.

Single directional light rendered as a *cascaded shadow map* (CSM): the
view frustum is split into depth slices, each slice gets its own
orthographic depth-only pass fitted to the slice's bounding sphere.
Cascade origins are snapped to the shadow-texel grid so sub-texel camera
motion does not re-align the cascade grid (no shadow swimming on static
geometry), and fragments inside a blend band at a cascade's far edge
sample both neighbouring cascades so switching cascades is continuous.

The main pass performs perspective-correct interpolation of UVs and
world positions, a z-buffer test, per-cascade shadow comparison with a
slope-scaled bias and 2x2 PCF, and Phong (ambient + diffuse + specular)
shading against a bilinear texture.  Color, depth and shadow-mask
buffers all come from the same rasterized (and, for skinned meshes, the
same deformed) geometry, so they always agree.

Near-plane clipping runs in clip space before rasterization, so geometry
crossing the near plane is cut correctly in every pass.
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
    def __init__(self, width, height, shadow_size=256, num_cascades=3):
        self.width = width
        self.height = height
        self.shadow_size = shadow_size
        self.num_cascades = max(1, int(num_cascades))

        # Main camera
        self.eye = (5.5, 4.2, 8.5)
        self.target = (0.0, 1.6, 0.0)
        self.up = (0.0, 1.0, 0.0)
        self.fovy = math.radians(50.0)
        self.near = 0.1
        self.far = 60.0
        self.shadow_far = 30.0        # cascades cover [near, shadow_far]

        # Directional light (travel direction, normalized).  The light
        # view is anchored at the world origin; only the per-cascade
        # orthographic boxes move (snapped to the texel grid).
        self.light_dir = math3d.normalize((-0.45, -1.0, -0.35))
        light_eye = math3d.scale(-14.0, self.light_dir)
        self.light_view = math3d.look_at(
            light_eye, (0.0, 0.0, 0.0), self.up)
        self.cascade_z_margin = 15.0

        # Phong coefficients
        self.ambient = 0.25
        self.diffuse = 0.8
        self.light_color = (1.0, 1.0, 0.95)
        self.shadow_bias = 0.0020
        self.shadow_slope_bias = 0.0035
        self.shadow_attenuation = 0.30   # ambient-only multiplier when shaded
        self.cascade_blend = 0.10        # fraction of slice depth to blend
        self.background = (24, 28, 40)

        # Frame buffers
        self.frame = bytearray(width * height * 3)
        self.zbuf = [1.0] * (width * height)
        self.depth_buf = [0.0] * (width * height)     # view depth, 0 = bg
        self.shadow_mask = bytearray(width * height)  # 255 = fully lit
        self.shadow_z = [
            [1.0] * (shadow_size * shadow_size)
            for _ in range(self.num_cascades)]

        self.set_camera(self.eye, self.target, self.up)

    # ------------------------------------------------------------------
    # Camera / cascade setup
    # ------------------------------------------------------------------
    def set_camera(self, eye, target, up=(0.0, 1.0, 0.0)):
        """Move the main camera and refit every cascade."""
        self.eye = tuple(eye)
        self.target = tuple(target)
        self.up = tuple(up)
        self.forward = math3d.normalize(
            math3d.sub(self.target, self.eye))
        self.view = math3d.look_at(self.eye, self.target, self.up)
        self.proj = math3d.perspective(
            self.fovy, self.width / self.height, self.near, self.far)
        self.vp = math3d.mul(self.proj, self.view)
        self._fit_cascades()

    def _split_depths(self):
        """Practical split scheme between uniform and logarithmic."""
        n = self.num_cascades
        near, far = self.near, min(self.far, self.shadow_far)
        lam = 0.5
        splits = [near]
        for i in range(1, n + 1):
            f = i / n
            log_split = near * (far / near) ** f
            uni_split = near + (far - near) * f
            splits.append(lam * log_split + (1.0 - lam) * uni_split)
        return splits

    def _frustum_slice_corners(self, d0, d1):
        """World-space corners of the view-frustum slice [d0, d1]."""
        aspect = self.width / self.height
        tan_half = math.tan(self.fovy * 0.5)
        # camera basis
        fwd = self.forward
        right = math3d.normalize(math3d.cross(fwd, self.up))
        up = math3d.cross(right, fwd)
        corners = []
        for d in (d0, d1):
            hh = d * tan_half
            hw = hh * aspect
            center = math3d.add(self.eye, math3d.scale(d, fwd))
            for sy in (-1.0, 1.0):
                for sx in (-1.0, 1.0):
                    corners.append(math3d.add(
                        center,
                        math3d.add(math3d.scale(sx * hw, right),
                                   math3d.scale(sy * hh, up))))
        return corners

    def _fit_cascades(self):
        """Fit one snapped orthographic box per cascade."""
        self.splits = self._split_depths()
        self.light_vp = []
        size = self.shadow_size
        margin = self.cascade_z_margin
        for i in range(self.num_cascades):
            corners = self._frustum_slice_corners(
                self.splits[i], self.splits[i + 1])
            pts = [math3d.transform(self.light_view, c) for c in corners]
            cx = sum(p[0] for p in pts) / 8.0
            cy = sum(p[1] for p in pts) / 8.0
            cz = sum(p[2] for p in pts) / 8.0
            radius = 0.0
            zmin = pts[0][2]
            zmax = pts[0][2]
            for p in pts:
                dx, dy = p[0] - cx, p[1] - cy
                r = math.sqrt(dx * dx + dy * dy)
                if r > radius:
                    radius = r
                zmin = min(zmin, p[2])
                zmax = max(zmax, p[2])
            # Quantize the radius so the texel size stays constant while
            # the camera moves, pad by a couple of texels so snapping can
            # never uncover a slice corner, then snap the window origin
            # to the texel grid.
            radius = math.ceil(radius * 4.0) / 4.0
            radius *= 1.0 + 4.0 / size
            extent = 2.0 * radius
            wpt = extent / size                  # world units per texel
            ox = math.floor((cx - radius) / wpt) * wpt
            oy = math.floor((cy - radius) / wpt) * wpt
            near_z = -zmax - margin
            far_z = -zmin + margin
            wpt_z = (far_z - near_z) / size
            near_z = math.floor(near_z / wpt_z) * wpt_z
            far_z = math.ceil(far_z / wpt_z) * wpt_z
            proj = math3d.ortho(ox, ox + extent, oy, oy + extent,
                                near_z, far_z)
            self.light_vp.append(math3d.mul(proj, self.light_view))

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

    def _build_triangles(self, mesh, mvp, stats=None):
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
            if stats is not None and len(clipped) != 3:
                stats['tris_clipped'] += 1
            for k in range(1, len(clipped) - 1):
                triangles.append((clipped[0], clipped[k], clipped[k + 1]))
        return triangles

    # ------------------------------------------------------------------
    # Shadow (depth-only) passes, one per cascade
    # ------------------------------------------------------------------
    def render_shadow(self, meshes):
        size = self.shadow_size
        for i in range(self.num_cascades):
            zbuf = self.shadow_z[i]
            for k in range(size * size):
                zbuf[k] = 1.0
            light_vp = self.light_vp[i]
            for mesh in meshes:
                for tri in self._build_triangles(mesh, light_vp):
                    pts = []
                    for vtx in tri:
                        cw = vtx[3]
                        if cw <= NEAR_EPS:
                            break
                        sx = (vtx[0] / cw * 0.5 + 0.5) * size
                        sy = (1.0 - (vtx[1] / cw * 0.5 + 0.5)) * size
                        pts.append((sx, sy, vtx[2] / cw))
                    else:
                        self._rasterize_depth(pts, zbuf, size)

    @staticmethod
    def _rasterize_depth(pts, zbuf, size):
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
    # Shadow lookup (cascade select + cross-cascade blend + 2x2 PCF)
    # ------------------------------------------------------------------
    def _cascade_lit_factor(self, cascade, world, cos_theta):
        """Lit fraction in [0, 1] for one cascade (1 = fully lit)."""
        lclip = math3d.clip_transform(self.light_vp[cascade], world)
        lcw = lclip[3]
        if lcw <= 0.0:
            return 1.0
        ndc_x = lclip[0] / lcw * 0.5 + 0.5
        ndc_y = lclip[1] / lcw * 0.5 + 0.5
        ndc_z = lclip[2] / lcw
        if not (0.0 <= ndc_x < 1.0 and 0.0 <= ndc_y < 1.0
                and 0.0 <= ndc_z <= 1.0):
            return 1.0
        size = self.shadow_size
        zbuf = self.shadow_z[cascade]
        bias = self.shadow_bias + self.shadow_slope_bias * (1.0 - cos_theta)
        fx = ndc_x * size
        fy = (1.0 - ndc_y) * size
        x0 = int(fx)
        y0 = int(fy)
        lit = 0
        for dy in (0, 1):
            ty = y0 + dy
            if ty >= size:
                ty = size - 1
            row = ty * size
            for dx in (0, 1):
                tx = x0 + dx
                if tx >= size:
                    tx = size - 1
                if zbuf[row + tx] + bias >= ndc_z:
                    lit += 1
        return lit * 0.25

    def _shadow_factor(self, world, view_depth, cos_theta):
        """Lit fraction with continuous transitions across cascades."""
        splits = self.splits
        n = self.num_cascades
        if view_depth >= splits[n]:
            return 1.0
        cascade = n - 1
        for i in range(n):
            if view_depth < splits[i + 1]:
                cascade = i
                break
        factor = self._cascade_lit_factor(cascade, world, cos_theta)
        if cascade < n - 1:
            edge = splits[cascade + 1]
            band = self.cascade_blend * (edge - splits[cascade])
            if view_depth > edge - band:
                nxt = self._cascade_lit_factor(cascade + 1, world, cos_theta)
                a = (view_depth - (edge - band)) / band
                factor = factor * (1.0 - a) + nxt * a
        return factor

    # ------------------------------------------------------------------
    # Main shaded pass
    # ------------------------------------------------------------------
    def render_frame(self, meshes):
        width, height = self.width, self.height
        frame, zbuf = self.frame, self.zbuf
        depth_buf, shadow_mask = self.depth_buf, self.shadow_mask
        for i in range(width * height):
            zbuf[i] = 1.0
            depth_buf[i] = 0.0
            shadow_mask[i] = 255
        br, bg, bb = self.background
        for i in range(0, len(frame), 3):
            frame[i] = br
            frame[i + 1] = bg
            frame[i + 2] = bb

        stats = {'tris_drawn': 0, 'tris_clipped': 0,
                 'fragments': 0, 'shadowed': 0}
        eye = self.eye
        forward = self.forward
        l_dir = self.light_dir
        # vector pointing toward the light (for diffuse term)
        to_light = (-l_dir[0], -l_dir[1], -l_dir[2])

        for mesh in meshes:
            texture = mesh.texture
            kd = mesh.kd
            specular = mesh.specular
            shininess = mesh.shininess
            triangles = self._build_triangles(mesh, self.vp, stats)
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
                    frame, zbuf, depth_buf, shadow_mask, stats,
                    eye, forward, to_light, width, height)

        stats['shadowed_pct'] = (
            100.0 * stats['shadowed'] / stats['fragments']
            if stats['fragments'] else 0.0)
        return bytes(frame), stats

    def _rasterize_shaded(self, screen, normal, texture, kd, specular,
                          shininess, frame, zbuf, depth_buf, shadow_mask,
                          stats, eye, forward, to_light, width, height):
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
        shadow_factor = self._shadow_factor

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

                        # View depth along the camera forward axis drives
                        # cascade selection.
                        view_depth = ((wx - eye[0]) * forward[0]
                                      + (wy - eye[1]) * forward[1]
                                      + (wz - eye[2]) * forward[2])
                        depth_buf[idx] = view_depth

                        lit = shadow_factor((wx, wy, wz), view_depth,
                                            n_dot_l)
                        mask_value = int(lit * 255.0 + 0.5)
                        shadow_mask[idx] = mask_value
                        if mask_value < 128:
                            stats['shadowed'] += 1

                        # Texture / base color.
                        if texture is not None:
                            tr, tg, tb = texture.sample(u, v)
                        else:
                            tr, tg, tb = 255.0, 255.0, 255.0
                        base_r = tr * kd[0]
                        base_g = tg * kd[1]
                        base_b = tb * kd[2]

                        shade = atten + (1.0 - atten) * lit
                        cr = base_r * (ambient * shade
                                       + diffuse * lit * lc[0]) \
                            + 255.0 * spec_term * lit * lc[0]
                        cg = base_g * (ambient * shade
                                       + diffuse * lit * lc[1]) \
                            + 255.0 * spec_term * lit * lc[1]
                        cb = base_b * (ambient * shade
                                       + diffuse * lit * lc[2]) \
                            + 255.0 * spec_term * lit * lc[2]

                        # Gamma encode and store.
                        frame[idx * 3] = min(
                            255, int(255.0 * ((cr / 255.0) ** inv_gamma)))
                        frame[idx * 3 + 1] = min(
                            255, int(255.0 * ((cg / 255.0) ** inv_gamma)))
                        frame[idx * 3 + 2] = min(
                            255, int(255.0 * ((cb / 255.0) ** inv_gamma)))
                px += 1

    # ------------------------------------------------------------------
    # Auxiliary buffer export
    # ------------------------------------------------------------------
    def depth_image(self):
        """Grayscale depth map: near geometry bright, background black."""
        out = bytearray(self.width * self.height * 3)
        far = min(self.far, self.shadow_far)
        span = far - self.near
        for i, d in enumerate(self.depth_buf):
            if d <= 0.0:
                continue
            t = (d - self.near) / span
            if t < 0.0:
                t = 0.0
            elif t > 1.0:
                t = 1.0
            g = int(255.0 * (1.0 - t) + 0.5)
            out[i * 3] = g
            out[i * 3 + 1] = g
            out[i * 3 + 2] = g
        return bytes(out)

    def shadow_mask_image(self):
        """Grayscale shadow mask: white = lit, black = fully shadowed."""
        out = bytearray(self.width * self.height * 3)
        mask = self.shadow_mask
        for i in range(self.width * self.height):
            v = mask[i]
            out[i * 3] = v
            out[i * 3 + 1] = v
            out[i * 3 + 2] = v
        return bytes(out)
