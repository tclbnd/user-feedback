"""Cascaded directional-light shadow mapping on top of pipeline.py.

The legacy :class:`pipeline.Renderer` is left untouched so the original
bar scene renders byte-identical frames.  This module adds
:class:`CascadeRenderer`, which keeps the same mesh / rasterization
contracts but replaces the single orthographic shadow map with:

* N directional-light cascades fitted to sub-frustums of the camera;
* a fixed light-space basis with texel-snapped orthographic origins, so
  sub-texel camera movement cannot re-grid a static world point;
* linear light-space depth and a world-space constant bias, making the
  depth comparison identical between cascades;
* 2x2 PCF and a blend band across each split for a continuous shadow
  factor when a fragment moves from one cascade to the next;
* exported linear depth and shadow-mask buffers for verification.

Only the Python standard library is used.
"""

import math

import math3d
from pipeline import NEAR_EPS, Renderer, RenderMesh  # noqa: F401 (RenderMesh re-export)


class Cascade:
    """One directional-light shadow map with a texel-snapped ortho frustum."""

    __slots__ = ('near', 'far', 'light_view', 'light_proj', 'light_vp',
                 'zbuf', 'size', 'texel_world', 'light_near', 'light_far')

    def __init__(self, near, far, size):
        self.near = near
        self.far = far
        self.size = size
        self.light_view = None
        self.light_proj = None
        self.light_vp = None
        self.zbuf = [math.inf] * (size * size)
        self.texel_world = 0.0
        self.light_near = 0.0
        self.light_far = 0.0


class CascadeRenderer(Renderer):
    """Perspective camera with N-cascade directional-light shadow maps.

    Inherits only the stateless geometry helpers (near-plane clipping and
    triangle projection); all frame state and shading are overridden.
    """

    def __init__(self, width, height, shadow_size=256, cascade_splits=None,
                 blend_fraction=0.12):
        self.width = width
        self.height = height
        self.shadow_size = shadow_size
        if cascade_splits is None:
            cascade_splits = (0.1, 6.0, 16.0, 42.0)
        self.cascade_splits = list(cascade_splits)
        self.blend_fraction = blend_fraction

        # Main camera
        self.eye = (5.5, 4.2, 8.5)
        self.target = (0.0, 1.6, 0.0)
        self.up = (0.0, 1.0, 0.0)
        self.fovy = math.radians(50.0)
        self.near = cascade_splits[0]
        self.far = cascade_splits[-1]
        self._update_camera()

        # Directional light: travel direction + a fixed world-space frame.
        self.light_dir = math3d.normalize((-0.45, -1.0, -0.35))
        self._build_light_frame()

        # Phong coefficients (same look as the legacy renderer)
        self.ambient = 0.25
        self.diffuse = 0.8
        self.light_color = (1.0, 1.0, 0.95)
        # Base receiver-plane offset in shadow-map depth quanta; the
        # effective bias is resolved per cascade (its depth range changes
        # every frame) and per fragment (light-incidence slope).
        self.shadow_bias_quanta = 1.5
        self.shadow_attenuation = 0.30
        self.background = (24, 28, 40)

        self.cascades = [
            Cascade(cascade_splits[i], cascade_splits[i + 1], shadow_size)
            for i in range(len(cascade_splits) - 1)]

        # Frame buffers
        self.frame = bytearray(width * height * 3)
        self.zbuf = [1.0] * (width * height)
        # Debug / verification outputs
        self.depth_linear = [self.far] * (width * height)
        self.shadow_mask = bytearray(b'\xff') * (width * height)

    # ------------------------------------------------------------------
    # Camera / light setup
    # ------------------------------------------------------------------
    def _update_camera(self):
        self.view = math3d.look_at(self.eye, self.target, self.up)
        self.proj = math3d.perspective(
            self.fovy, self.width / self.height, self.near, self.far)
        self.vp = math3d.mul(self.proj, self.view)

    def set_camera(self, eye, target=None, up=None):
        self.eye = tuple(eye)
        if target is not None:
            self.target = tuple(target)
        if up is not None:
            self.up = tuple(up)
        self._update_camera()

    def _build_light_frame(self):
        """Fixed orthonormal basis (right, up, forward) for the light."""
        forward = tuple(-c for c in self.light_dir)   # direction rays travel
        ref_up = (0.0, 1.0, 0.0)
        right = math3d.normalize(math3d.cross(ref_up, forward))
        if right == (0.0, 0.0, 0.0):
            right = (1.0, 0.0, 0.0)
        light_up = math3d.normalize(math3d.cross(forward, right))
        self.light_right = right
        self.light_up = light_up
        self.light_forward = forward
        self._light_view_rotation = [
            right[0], right[1], right[2], 0.0,
            light_up[0], light_up[1], light_up[2], 0.0,
            -forward[0], -forward[1], -forward[2], 0.0,
            0.0, 0.0, 0.0, 1.0]

    def _light_view_at(self, origin):
        m = list(self._light_view_rotation)
        m[3] = -math3d.dot(self.light_right, origin)
        m[7] = -math3d.dot(self.light_up, origin)
        m[11] = math3d.dot(self.light_forward, origin)
        return m

    def _frustum_corners_view(self, near, far):
        """8 corners of the main camera's sub-frustum, in view space."""
        tan_half = math.tan(self.fovy * 0.5)
        aspect = self.width / self.height
        x_near = tan_half * near * aspect
        y_near = tan_half * near
        x_far = tan_half * far * aspect
        y_far = tan_half * far
        return [
            (-x_near, -y_near, -near), (x_near, -y_near, -near),
            (x_near, y_near, -near), (-x_near, y_near, -near),
            (-x_far, -y_far, -far), (x_far, -y_far, -far),
            (x_far, y_far, -far), (-x_far, y_far, -far),
        ]

    def fit_cascades(self):
        """Recompute every light frustum from the current camera frustum."""
        view_inv = math3d.inverse(self.view)
        for cas in self.cascades:
            corners_world = [
                math3d.transform(view_inv, c)
                for c in self._frustum_corners_view(cas.near, cas.far)]
            min_x = min_y = min_z = math.inf
            max_x = max_y = max_z = -math.inf
            for cw in corners_world:
                lx = math3d.dot(self.light_right, cw)
                ly = math3d.dot(self.light_up, cw)
                # World-axis depth along the rays' travel direction.  The
                # ortho projection maps this axis to NDC z, so the
                # renderer's near + ndc*(far-near) reconstruction returns
                # exactly this value (no extra sign flip anywhere).
                lz = math3d.dot(self.light_forward, cw)
                min_x, max_x = min(min_x, lx), max(max_x, lx)
                min_y, max_y = min(min_y, ly), max(max_y, ly)
                min_z, max_z = min(min_z, lz), max(max_z, lz)

            extent = max(max_x - min_x, max_y - min_y)
            center_x = 0.5 * (min_x + max_x)
            center_y = 0.5 * (min_y + max_y)
            texel = extent / self.shadow_size
            cas.texel_world = texel
            # Texel snapping: align the frustum origin to whole texels so
            # static world points keep the same shadow texel while the
            # camera makes sub-texel movements.
            origin_x = math.floor(center_x / texel) * texel
            origin_y = math.floor(center_y / texel) * texel
            half = 0.5 * self.shadow_size * texel
            left = origin_x - half
            right = origin_x + half
            bottom = origin_y - half
            top = origin_y + half
            # Depth range with padding; z is never snapped (the depth
            # compare is independent of the xy grid alignment).  Stored
            # linear depth follows the light view-space axis,
            # lin_z = -dot(forward, point), matching the NDC reconstruction.
            pad_z = max(2.0, 0.25 * (max_z - min_z))
            l_near = min_z - pad_z
            l_far = max_z + pad_z
            if l_far - l_near < 1.0:
                l_far = l_near + 1.0
            cas.light_near = l_near
            cas.light_far = l_far

            origin = (
                self.light_right[0] * origin_x
                + self.light_up[0] * origin_y,
                self.light_right[1] * origin_x
                + self.light_up[1] * origin_y,
                self.light_right[2] * origin_x
                + self.light_up[2] * origin_y)
            cas.light_view = self._light_view_at(origin)
            cas.light_proj = math3d.ortho(
                left, right, bottom, top, l_near, l_far)
            cas.light_vp = math3d.mul(cas.light_proj, cas.light_view)
            for i in range(self.shadow_size * self.shadow_size):
                cas.zbuf[i] = math.inf

    # ------------------------------------------------------------------
    # Depth-only shadow passes (linear light-space depth)
    # ------------------------------------------------------------------
    def render_shadow(self, meshes):
        self.fit_cascades()
        size = self.shadow_size
        for cas in self.cascades:
            zbuf = cas.zbuf
            for mesh in meshes:
                for tri in self._build_triangles(mesh, cas.light_vp):
                    pts = []
                    for vtx in tri:
                        cw = vtx[3]
                        if cw <= NEAR_EPS:
                            break
                        # Orthographic light: clip w == 1 and clip z is
                        # NDC depth in [0, 1] with ndc = (lin-near)/
                        # (far-near).  Invert it to world-axis linear
                        # depth so the bias is resolution/frustum agnostic
                        # and identical across cascades.
                        ndc_z = vtx[2] / cw
                        lin_z = cas.light_near + ndc_z * (
                            cas.light_far - cas.light_near)
                        sx = (vtx[0] / cw * 0.5 + 0.5) * size
                        sy = (1.0 - (vtx[1] / cw * 0.5 + 0.5)) * size
                        pts.append((sx, sy, lin_z))
                    else:
                        self._rasterize_depth_linear(pts, zbuf, size)

    @staticmethod
    def _rasterize_depth_linear(pts, zbuf, size):
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
        inv_denom = 1.0 / denom
        for py in range(min_y, max_y + 1):
            row_base = py * size
            px = min_x
            while px <= max_x:
                l1 = (y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)
                l2 = (y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)
                l3 = denom - l1 - l2
                if positive:
                    inside = l1 >= 0.0 and l2 >= 0.0 and l3 >= 0.0
                else:
                    inside = l1 <= 0.0 and l2 <= 0.0 and l3 <= 0.0
                if inside:
                    b1 = l1 * inv_denom
                    b2 = l2 * inv_denom
                    b3 = l3 * inv_denom
                    z = z1 * b1 + z2 * b2 + z3 * b3
                    idx = row_base + px
                    if z < zbuf[idx]:
                        zbuf[idx] = z
                px += 1

    # ------------------------------------------------------------------
    # Shadow factor with 2x2 PCF and cascade blend bands
    # ------------------------------------------------------------------
    def _cascade_bias(self, cas, normal=None):
        """Adaptive world-space depth bias for one cascade/fragment.

        A constant bias cannot work across cascades because their light
        depth ranges differ by an order of magnitude.  Scale by the
        cascade's depth quantum and add the standard slope term so a
        receiver seen at a grazing light angle does not self-acne.
        """
        quantum = (cas.light_far - cas.light_near) / self.shadow_size
        base = self.shadow_bias_quanta * quantum
        if normal is None:
            return base
        cos_i = abs(math3d.dot(normal, self.light_dir))
        slope = 1.0 / max(cos_i, 1e-3)
        return base * min(slope, 8.0)

    def _pcf_factor(self, cas, world, lin_z, normal=None):
        """Continuous shadow factor in [0, 1] (1 = fully lit)."""
        size = self.shadow_size
        # Project through the FULL snapped light view-projection.  Using
        # light_proj alone would drop the view translation and offset every
        # query by half a texel or more; the depth rasterizer uses light_vp.
        clip = math3d.clip_transform(cas.light_vp, world)
        cw = clip[3]
        ndc_x = clip[0] / cw
        ndc_y = clip[1] / cw
        fx = (ndc_x * 0.5 + 0.5) * size - 0.5
        fy = (0.5 - ndc_y * 0.5) * size - 0.5
        x0 = math.floor(fx)
        y0 = math.floor(fy)
        tx = fx - x0
        ty = fy - y0
        zbuf = cas.zbuf
        ref_z = lin_z - self._cascade_bias(cas, normal)
        weights = ((x0, y0, (1.0 - tx) * (1.0 - ty)),
                   (x0 + 1, y0, tx * (1.0 - ty)),
                   (x0, y0 + 1, (1.0 - tx) * ty),
                   (x0 + 1, y0 + 1, tx * ty))
        lit = 0.0
        wsum = 0.0
        for sx, sy, w in weights:
            if 0 <= sx < size and 0 <= sy < size:
                blocked = 1.0 if zbuf[sy * size + sx] < ref_z else 0.0
                lit += w * (1.0 - blocked)
                wsum += w
        if wsum == 0.0:
            return 1.0
        return lit / wsum

    def shadow_factor(self, world, normal=None):
        """Blended shadow factor for a world point."""
        view_z = -math3d.transform(self.view, world)[2]
        count = len(self.cascades)
        idx = count - 1
        for i, cas in enumerate(self.cascades):
            if view_z <= cas.far:
                idx = i
                break
        cas = self.cascades[idx]
        lin_z = math3d.dot(self.light_forward, world)
        factor = self._pcf_factor(cas, world, lin_z, normal)

        # Blend band across the split into the next cascade.
        blend = 0.0
        if idx < count - 1:
            band = (cas.far - cas.near) * self.blend_fraction
            dist = cas.far - view_z
            if dist < band:
                nxt = self.cascades[idx + 1]
                factor_next = self._pcf_factor(
                    nxt, world, lin_z, normal)
                blend = 1.0 - max(0.0, dist) / band if band > 0.0 else 1.0
                factor = factor * (1.0 - blend) + factor_next * blend
        return factor, idx, blend

    # ------------------------------------------------------------------
    # Main shaded pass
    # ------------------------------------------------------------------
    def render_frame(self, meshes):
        width, height = self.width, self.height
        frame, zbuf = self.frame, self.zbuf
        for i in range(width * height):
            zbuf[i] = 1.0
            self.depth_linear[i] = self.far
        self.shadow_mask[:] = b'\xff' * (width * height)
        br, bg, bb = self.background
        for i in range(0, len(frame), 3):
            frame[i] = br
            frame[i + 1] = bg
            frame[i + 2] = bb

        stats = {'tris_drawn': 0, 'fragments': 0,
                 'shadowed': 0, 'cascade_hits': [0] * len(self.cascades),
                 'blend_hits': 0}
        eye = self.eye
        to_light = tuple(-c for c in self.light_dir)
        inv_gamma = 1.0 / 2.2
        lc = self.light_color
        ambient = self.ambient
        diffuse = self.diffuse
        atten = self.shadow_attenuation

        for mesh in meshes:
            texture = mesh.texture
            kd = mesh.kd
            specular = mesh.specular
            shininess = mesh.shininess
            triangles = self._build_triangles(mesh, self.vp)
            stats['tris_drawn'] += len(triangles)

            for tri in triangles:
                w1 = (tri[0][4], tri[0][5], tri[0][6])
                w2 = (tri[1][4], tri[1][5], tri[1][6])
                w3 = (tri[2][4], tri[2][5], tri[2][6])
                e1 = math3d.sub(w2, w1)
                e2 = math3d.sub(w3, w1)
                normal = math3d.normalize(math3d.cross(e1, e2))
                if normal == (0.0, 0.0, 0.0):
                    continue

                screen = []
                for vtx in tri:
                    cw = vtx[3]
                    inv_w = 1.0 / cw
                    sx = (vtx[0] * inv_w * 0.5 + 0.5) * width
                    sy = (1.0 - (vtx[1] * inv_w * 0.5 + 0.5)) * height
                    screen.append((
                        sx, sy, vtx[2] * inv_w, inv_w,
                        vtx[7] * inv_w, vtx[8] * inv_w,
                        vtx[4] * inv_w, vtx[5] * inv_w, vtx[6] * inv_w))
                self._rasterize_csm(
                    screen, normal, texture, kd, specular, shininess,
                    frame, zbuf, stats, eye, to_light, lc, ambient,
                    diffuse, atten, inv_gamma, width, height)

        stats['shadowed_pct'] = (
            100.0 * stats['shadowed'] / stats['fragments']
            if stats['fragments'] else 0.0)
        return bytes(frame), stats

    def _rasterize_csm(self, screen, normal, texture, kd, specular,
                       shininess, frame, zbuf, stats, eye, to_light,
                       lc, ambient, diffuse, atten, inv_gamma,
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

        face_center = (
            (wx1 / iw1 + wx2 / iw2 + wx3 / iw3) / 3.0,
            (wy1 / iw1 + wy2 / iw2 + wy3 / iw3) / 3.0,
            (wz1 / iw1 + wz2 / iw2 + wz3 / iw3) / 3.0)
        view_vec = math3d.sub(eye, face_center)
        if math3d.dot(normal, view_vec) < 0.0:
            normal = (-normal[0], -normal[1], -normal[2])
        n_dot_l = max(0.0, math3d.dot(normal, to_light))
        view_dir = math3d.normalize(view_vec)
        half_vec = math3d.normalize(math3d.add(to_light, view_dir))
        n_dot_h = max(0.0, math3d.dot(normal, half_vec))
        spec_term = specular * (n_dot_h ** shininess)

        for py in range(min_y, max_y + 1):
            row_base = py * width
            px = min_x
            while px <= max_x:
                l1 = (y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)
                l2 = (y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)
                l3 = denom - l1 - l2
                if positive:
                    inside = l1 >= 0.0 and l2 >= 0.0 and l3 >= 0.0
                else:
                    inside = l1 <= 0.0 and l2 <= 0.0 and l3 <= 0.0
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

                        view_pos = math3d.transform(
                            self.view, (wx, wy, wz))
                        self.depth_linear[idx] = -view_pos[2]

                        factor, cas_idx, blend = self.shadow_factor(
                            (wx, wy, wz), normal)
                        stats['cascade_hits'][cas_idx] += 1
                        if blend > 0.0:
                            stats['blend_hits'] += 1
                        if factor < 0.5:
                            stats['shadowed'] += 1
                        # mask: 0 = fully shadowed, 255 = fully lit
                        self.shadow_mask[idx] = int(
                            max(0.0, min(1.0, factor)) * 255.0 + 0.5)

                        if texture is not None:
                            tr, tg, tb = texture.sample(u, v)
                        else:
                            tr, tg, tb = 255.0, 255.0, 255.0
                        base_r = tr * kd[0]
                        base_g = tg * kd[1]
                        base_b = tb * kd[2]

                        # Continuous shading: ambient-only when fully
                        # shadowed, full lighting when fully lit.
                        shade = ambient * (
                            atten + (1.0 - atten) * factor) \
                            + diffuse * n_dot_l * factor
                        cr = base_r * shade + 255.0 * spec_term \
                            * factor * lc[0]
                        cg = base_g * shade + 255.0 * spec_term \
                            * factor * lc[1]
                        cb = base_b * shade + 255.0 * spec_term \
                            * factor * lc[2]

                        frame[idx * 3] = min(
                            255, int(255.0 * ((cr / 255.0) ** inv_gamma)))
                        frame[idx * 3 + 1] = min(
                            255, int(255.0 * ((cg / 255.0) ** inv_gamma)))
                        frame[idx * 3 + 2] = min(
                            255, int(255.0 * ((cb / 255.0) ** inv_gamma)))
                px += 1

    # ------------------------------------------------------------------
    # Debug buffers
    # ------------------------------------------------------------------
    def depth_image(self):
        """Linear camera depth encoded as an 8-bit grayscale RGB body."""
        out = bytearray(self.width * self.height * 3)
        max_d = max(self.near, self.far)
        for i, d in enumerate(self.depth_linear):
            if d >= self.far:
                g = 0
            else:
                g = int(max(0.0, min(1.0, d / max_d)) * 255.0 + 0.5)
            out[i * 3] = out[i * 3 + 1] = out[i * 3 + 2] = g
        return bytes(out)

    def mask_image(self):
        """Shadow factor (0..255) expanded to an 8-bit grayscale RGB body."""
        out = bytearray(self.width * self.height * 3)
        for i, m in enumerate(self.shadow_mask):
            out[i * 3] = out[i * 3 + 1] = out[i * 3 + 2] = m
        return bytes(out)
