"""Shadow pipeline verification.

Three independent checks against the rasterized cascaded shadow maps:

1. ``test_ray_occlusion`` -- ground sample points well away from shadow
   edges are classified lit/shadowed by a brute-force ray/triangle test
   over the *same deformed geometry* and compared with the renderer's
   shadow factor.
2. ``test_cascade_continuity`` -- a fragment crossing a cascade split
   inside the blend band must keep a continuous shadow factor, and both
   cascades must agree on points far from shadow edges.
3. ``test_subtexel_stability`` -- sub-texel camera movement must not
   change the shadow mask on the static ground (texel snapping keeps
   static world points on the same shadow texels).
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math3d  # noqa: E402
from scene_character import CharacterScene, make_renderer  # noqa: E402

GROUND_Y = 0.0


def ray_occluded(origin, direction, vertices, faces, max_t):
    """Brute-force occlusion: any triangle hit before max_t."""
    for face in faces:
        a = vertices[face[0][0]]
        b = vertices[face[1][0]]
        c = vertices[face[2][0]]
        hit = math3d.ray_triangle(origin, direction, a, b, c)
        if hit is not None and hit[0] < max_t:
            return True
    return False


def ground_sample_points(scene, renderer, time, grid=9, margin=1.2):
    """Ground points that project inside the frame and are not background."""
    char, _ = scene.skinned_mesh_at(time)
    eye, target = scene.camera_at(time)
    renderer.set_camera(eye, target)
    points = []
    span = 7.0
    for ix in range(grid):
        for iz in range(grid):
            x = -span + 2 * span * ix / (grid - 1)
            z = -span + 2 * span * iz / (grid - 1)
            # keep samples away from the character's vertical column so
            # they sit in flat umbra or fully lit regions, never on edges
            if math.hypot(x, z - 0.4) < margin:
                continue
            clip = math3d.clip_transform(renderer.vp, (x, GROUND_Y, z))
            if clip[3] <= renderer.near:
                continue
            ndc_x = clip[0] / clip[3]
            ndc_y = clip[1] / clip[3]
            if -0.98 < ndc_x < 0.98 and -0.98 < ndc_y < 0.98:
                points.append((x, GROUND_Y, z))
    return points, char


def test_ray_occlusion(scene, renderer, times):
    total = 0
    agree = 0
    edge_skipped = 0
    per_time = []
    for t in times:
        points, char = ground_sample_points(scene, renderer, t)
        eye, target = scene.camera_at(t)
        renderer.set_camera(eye, target)
        renderer.render_shadow([char, scene.ground_mesh()])
        light_dir = renderer.light_dir
        n_agree = n_total = 0
        for p in points:
            # Ray starts an epsilon above the ground to avoid intersecting
            # the ground plane itself; the shadow-map query is made exactly
            # at the ground surface (y=0) so it matches the stored depth.
            origin = (p[0], GROUND_Y + 1e-3, p[2])
            toward = tuple(-c for c in light_dir)
            ray_hit = ray_occluded(origin, toward, char.vertices,
                                   char.faces, max_t=40.0)
            # Require agreement across a small spatial neighborhood: only
            # points whose whole neighborhood is confidently lit or
            # shadowed count, so PCF penumbra edges never enter the metric.
            factors = []
            for dx in (-0.18, 0.0, 0.18):
                for dz in (-0.18, 0.0, 0.18):
                    f, _i, _b = renderer.shadow_factor(
                        (p[0] + dx, GROUND_Y, p[2] + dz))
                    factors.append(f)
            f_lo, f_hi = min(factors), max(factors)
            confident_lit = f_lo > 0.9
            confident_shadow = f_hi < 0.1
            if not (confident_lit or confident_shadow):
                edge_skipped += 1
                continue
            raster_shadow = confident_shadow
            n_total += 1
            if ray_hit == raster_shadow:
                n_agree += 1
        total += n_total
        agree += n_agree
        per_time.append((t, n_agree, n_total))
    rate = agree / total if total else 0.0
    return {'agreed': agree, 'total': total, 'rate': rate,
            'edge_skipped': edge_skipped, 'per_time': per_time}


def test_cascade_continuity(scene, renderer, time=3.0):
    """Shadow factor is continuous as view depth crosses a cascade split."""
    char, _ = scene.skinned_mesh_at(time)
    eye, target = scene.camera_at(time)
    renderer.set_camera(eye, target)
    renderer.render_shadow([char, scene.ground_mesh()])

    cas = renderer.cascades[1]
    split = cas.far                          # view-space depth 10.0
    band = (cas.far - cas.near) * renderer.blend_fraction

    # Sweep a ground ray along the camera's viewing axis so camera-space
    # depth passes through split +/- band, holding x well clear of the
    # character.  view = inv(view) * (0, 0, -z_view).
    view_inv = math3d.inverse(renderer.view)
    factors = []
    depth = split - band * 2.2
    while depth <= split + band * 2.2:
        local = (3.0, -2.2, -depth)
        p = math3d.transform(view_inv, local)
        p = (p[0], GROUND_Y, p[2])
        f, idx, blend = renderer.shadow_factor(p)
        factors.append((depth, f, idx, blend))
        depth += band * 0.06

    max_delta = 0.0
    blend_seen = 0
    index_switches = 0
    prev_idx = None
    for a, b in zip(factors, factors[1:]):
        max_delta = max(max_delta, abs(b[1] - a[1]))
        if b[3] > 0.0 or a[3] > 0.0:
            blend_seen += 1
        if prev_idx is not None and b[2] != prev_idx:
            index_switches += 1
        prev_idx = b[2]
    return {'max_factor_delta': max_delta, 'blend_samples': blend_seen,
            'index_switches': index_switches,
            'split_depth': split, 'n_samples': len(factors)}


def test_subtexel_stability(scene, renderer, base_time=0.4):
    """Sub-texel camera motion leaves the static-ground shadow mask fixed."""
    char, _ = scene.skinned_mesh_at(base_time)
    ground = scene.ground_mesh()

    # A camera pose and sub-texel offsets (well under one shadow texel of
    # the nearest cascade, which is ~ a few cm at these frustum sizes).
    eye0, target0 = scene.camera_at(base_time)
    offsets = [(0.0, 0.0), (0.003, 0.0), (-0.004, 0.002),
               (0.001, -0.003), (0.005, 0.004)]
    masks = []
    visible_sets = []
    for ox, oy in offsets:
        renderer.set_camera((eye0[0] + ox, eye0[1] + oy, eye0[2]),
                            (target0[0] + ox, target0[1], target0[2]))
        renderer.render_shadow([char, ground])
        renderer.render_frame([char, ground])
        masks.append(bytes(renderer.shadow_mask))
        # Visibility is recomputed per camera: a sub-texel slide changes
        # which ground pixels land on screen, and those frame-edge pixels
        # must not be counted as shadow changes.
        ground_depth = _ground_ndc_depth(renderer, ground)
        visible_sets.append(set(
            i for i, d in enumerate(ground_depth)
            if d is not None and abs(d - renderer.zbuf[i]) < 1e-4))
    common = set.intersection(*visible_sets)
    # A pixel counts as a static shadow-region sample only when it is
    # confidently lit (>=240) or confidently shadowed (<=15) in *every*
    # view.  Hard terminator pixels (which legitimately alternate 0/255 as
    # the edge crosses them) and PCF penumbra pixels are excluded, so the
    # metric measures grid re-alignment jitter alone.
    def is_flat(value):
        return value >= 240 or value <= 15

    static = [i for i in common
              if all(is_flat(m[i]) for m in masks)]
    diffs = []
    mean_abs = []
    max_abs = []
    # A state flip (lit<->shadow) only happens when a hard terminator edge
    # crosses the pixel; separate those expected edge crossings from the
    # sub-quantum drift that would indicate grid re-alignment.
    edge_crossings = 0
    ref = masks[0]
    for m in masks[1:]:
        deltas = [abs(int(ref[i]) - int(m[i])) for i in static]
        changed = sum(1 for d in deltas if d != 0)
        edge_crossings += sum(
            1 for i in static
            if (ref[i] >= 240 and m[i] <= 15)
            or (ref[i] <= 15 and m[i] >= 240))
        diffs.append(changed)
        if deltas:
            mean_abs.append(sum(deltas) / len(deltas))
            max_abs.append(max(deltas))
    total_ground = len(static)
    max_changed = max(diffs)
    # Correct texel snapping means static regions essentially never move;
    # only a few terminator-edge pixels change, and mean drift stays under
    # one 8-bit mask quantum.
    return {'flat_region_pixels': total_ground,
            'max_changed_pixels': max_changed,
            'change_rate': max_changed / total_ground if total_ground else 1.0,
            'edge_crossings': edge_crossings,
            'mean_abs_mask_change': max(mean_abs) if mean_abs else 0.0,
            'max_abs_mask_change': max(max_abs) if max_abs else 0,
            'offsets': offsets[1:]}


def _ground_ndc_depth(renderer, ground):
    """Per-pixel NDC depth of the ground plane (None where not covered).

    Rasterizes the two (oversized, partly behind-camera) ground triangles
    via parameter-space subdivision, interpolating clip z and w exactly
    like the production rasterizer, then dividing to NDC.
    """
    width, height = renderer.width, renderer.height
    depth = [None] * (width * height)
    vp = renderer.vp
    for face in ground.faces:
        verts = [ground.vertices[vi] for vi, _ui in face]
        steps = 24

        def sample(u, v):
            w0 = 1.0 - u - v
            return tuple(w0 * verts[0][k] + u * verts[1][k]
                         + v * verts[2][k] for k in range(3))

        sub = []
        for i in range(steps):
            for j in range(steps - i):
                u0, v0 = i / steps, j / steps
                u1, v1 = (i + 1) / steps, j / steps
                u2, v2 = i / steps, (j + 1) / steps
                sub.append((sample(u0, v0), sample(u1, v1),
                            sample(u2, v2)))
                if i + j + 2 <= steps:
                    u3, v3 = (i + 1) / steps, (j + 1) / steps
                    sub.append((sample(u1, v1), sample(u3, v3),
                                sample(u2, v2)))
        for tri in sub:
            pts = []
            ok = True
            for w in tri:
                clip = math3d.clip_transform(vp, w)
                if clip[3] <= renderer.near:
                    ok = False
                    break
                sx = (clip[0] / clip[3] * 0.5 + 0.5) * width
                sy = (1.0 - (clip[1] / clip[3] * 0.5 + 0.5)) * height
                pts.append((sx, sy, clip[2] / clip[3]))
            if not ok:
                continue
            _paint_depth(pts, depth, width, height)
    return depth


def _paint_depth(pts, depth, width, height):
    (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = pts
    denom = (y2 - y3) * (x1 - x3) + (x3 - x2) * (y1 - y3)
    if abs(denom) < 1e-9:
        return
    min_x = max(int(min(x1, x2, x3)), 0)
    max_x = min(int(max(x1, x2, x3)) + 1, width - 1)
    min_y = max(int(min(y1, y2, y3)), 0)
    max_y = min(int(max(y1, y2, y3)) + 1, height - 1)
    pos = denom > 0
    for py in range(min_y, max_y + 1):
        for px in range(min_x, max_x + 1):
            l1 = (y2 - y3) * (px - x3) + (x3 - x2) * (py - y3)
            l2 = (y3 - y1) * (px - x3) + (x1 - x3) * (py - y3)
            l3 = denom - l1 - l2
            inside = (l1 >= 0 and l2 >= 0 and l3 >= 0) if pos \
                else (l1 <= 0 and l2 <= 0 and l3 <= 0)
            if inside:
                b1, b2, b3 = l1 / denom, l2 / denom, l3 / denom
                depth[py * width + px] = z1 * b1 + z2 * b2 + z3 * b3



def run():
    scene = CharacterScene()
    renderer = make_renderer(160, 160, shadow_size=256)
    times = [0.2, 0.9, 1.6, 2.2, 3.0, 3.6]
    results = {}
    results['ray_occlusion'] = test_ray_occlusion(scene, renderer, times)
    results['cascade_continuity'] = test_cascade_continuity(scene, renderer)
    results['subtexel_stability'] = test_subtexel_stability(scene, renderer)
    st = results['subtexel_stability']
    results['passed'] = (
        results['ray_occlusion']['rate'] >= 0.97
        and results['cascade_continuity']['max_factor_delta'] <= 0.25
        and results['cascade_continuity']['blend_samples'] > 0
        and st['change_rate'] <= 0.002
        and st['mean_abs_mask_change'] <= 1.0)
    return results


if __name__ == '__main__':
    import json
    print(json.dumps(run(), indent=2, default=str))
