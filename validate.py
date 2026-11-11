"""Validation harness for the upgraded pipeline (stdlib only).

Four independent checks, each reported with a PASS/FAIL verdict:

A. Joint endpoints — a tiny analytic rig (non-uniform root scale,
   sign-flipped key quaternions, translation/rotation/scale tracks)
   is evaluated through skinning.Rig and compared against closed-form
   trigonometry computed here; the character rig's joint world
   positions are additionally checked against an independent forward-
   kinematics implementation that reads character_rig.json directly.
   Cross-fade sweeps are sampled densely to prove quaternion
   hemisphere alignment keeps endpoint paths continuous.

B. Skinned vertices — selected character vertices are compared against
   an independent LBS implementation (own track sampling, own affine
   inverse) driven straight from the JSON file.

C. Occlusion — ground sample points far from shadow edges are
   classified by ray/triangle intersection (Moeller-Trumbore) against
   the deformed character and compared with the rendered shadow mask
   at the projected pixel.

D. Stability — with the pose frozen, the camera is nudged by sub-texel
   offsets; fixed world-space ground points must keep their shadow
   mask values (cascade windows are texel-snapped, so nothing may
   swim).

Run:  python3 validate.py
"""

import json
import math
import os
import sys
import tempfile

import blend_scene
import math3d
from pipeline import Renderer
from skinning import load_rig


TO_LIGHT = None  # filled in main() from the renderer


# ----------------------------------------------------------------------
# Independent math (deliberately re-implemented, not imported from
# skinning.py, so the checks are meaningful)
# ----------------------------------------------------------------------
def quat_to_rot3(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    x, y, z, w = x / n, y / n, z / n, w / n
    return [
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ]


def mat3_mul_vec(r, v):
    return tuple(r[i][0] * v[0] + r[i][1] * v[1] + r[i][2] * v[2]
                 for i in range(3))


def trs_compose(parent, t, q, s):
    """parent: 4x4 row-major; returns 4x4 = parent * T*R*S."""
    r = quat_to_rot3(q)
    out = [[0.0] * 4 for _ in range(4)]
    local = [[r[i][j] * s[j] for j in range(3)] + [t[i]] for i in range(3)]
    local.append([0.0, 0.0, 0.0, 1.0])
    for i in range(4):
        for j in range(4):
            out[i][j] = sum(parent[i][k] * local[k][j] for k in range(4))
    return out


def invert_affine_ind(m):
    """Adjugate-based inverse of a 4x4 affine matrix (list of rows)."""
    a = [[m[i][j] for j in range(3)] for i in range(3)]
    t = [m[0][3], m[1][3], m[2][3]]
    det = (a[0][0] * (a[1][1] * a[2][2] - a[1][2] * a[2][1])
           - a[0][1] * (a[1][0] * a[2][2] - a[1][2] * a[2][0])
           + a[0][2] * (a[1][0] * a[2][1] - a[1][1] * a[2][0]))
    cof = [[0.0] * 3 for _ in range(3)]
    for i in range(3):
        for j in range(3):
            rows = [r for r in range(3) if r != i]
            cols = [c for c in range(3) if c != j]
            minor = (a[rows[0]][cols[0]] * a[rows[1]][cols[1]]
                     - a[rows[0]][cols[1]] * a[rows[1]][cols[0]])
            cof[i][j] = ((-1) ** (i + j)) * minor
    inv3 = [[cof[j][i] / det for j in range(3)] for i in range(3)]
    out = [[0.0] * 4 for _ in range(4)]
    for i in range(3):
        for j in range(3):
            out[i][j] = inv3[i][j]
        out[i][3] = -sum(inv3[i][k] * t[k] for k in range(3))
    out[3][3] = 1.0
    return out


def mat4_point(m, p):
    return tuple(m[i][0] * p[0] + m[i][1] * p[1] + m[i][2] * p[2] + m[i][3]
                 for i in range(3))


def slerp_ind(a, b, f):
    if sum(a[i] * b[i] for i in range(4)) < 0.0:
        b = tuple(-c for c in b)
    d = max(-1.0, min(1.0, sum(a[i] * b[i] for i in range(4))))
    if d > 0.9995:
        q = tuple(a[i] + f * (b[i] - a[i]) for i in range(4))
    else:
        th = math.acos(d)
        q = tuple((math.sin((1 - f) * th) * a[i] + math.sin(f * th) * b[i])
                  / math.sin(th) for i in range(4))
    n = math.sqrt(sum(c * c for c in q))
    return tuple(c / n for c in q)


def sample_track_ind(times, values, t, is_quat):
    if t <= times[0]:
        return tuple(values[0])
    if t >= times[-1]:
        return tuple(values[-1])
    i = 0
    while times[i + 1] < t:
        i += 1
    f = (t - times[i]) / (times[i + 1] - times[i])
    if is_quat:
        return slerp_ind(tuple(values[i]), tuple(values[i + 1]), f)
    return tuple(values[i][k] + f * (values[i + 1][k] - values[i][k])
                 for k in range(3))


def independent_local_pose(rig_json, specs):
    """Per-joint blended local TRS, computed independently from JSON."""
    joints = rig_json['joints']
    per_action = []
    for action_name, time, weight in specs:
        action = rig_json['actions'][action_name]
        if action.get('loop', True):
            t = time % action['duration']
        else:
            t = max(0.0, min(time, action['duration']))
        pose = {}
        for joint in joints:
            name = joint['name']
            bt = tuple(joint['position'])
            bq = tuple(joint.get('rotation', (0.0, 0.0, 0.0, 1.0)))
            bs = tuple(joint.get('scale', (1.0, 1.0, 1.0)))
            track = action['tracks'].get(name)
            if track is None:
                pose[name] = (bt, bq, bs)
                continue
            times = track['times']
            lt = (sample_track_ind(times, track['translations'], t, False)
                  if 'translations' in track else bt)
            lq = (sample_track_ind(times, track['rotations'], t, True)
                  if 'rotations' in track else bq)
            ls = (sample_track_ind(times, track['scales'], t, False)
                  if 'scales' in track else bs)
            pose[name] = (lt, lq, ls)
        per_action.append((pose, weight))
    total = sum(w for _, w in per_action)
    out = {}
    for joint in joints:
        name = joint['name']
        ref = per_action[0][0][name][1]
        at = [0.0] * 3
        aq = [0.0] * 4
        as_ = [0.0] * 3
        for pose, w in per_action:
            w /= total
            lt, lq, ls = pose[name]
            if sum(lq[i] * ref[i] for i in range(4)) < 0.0:
                lq = tuple(-c for c in lq)
            for i in range(3):
                at[i] += w * lt[i]
                as_[i] += w * ls[i]
            for i in range(4):
                aq[i] += w * lq[i]
        n = math.sqrt(sum(c * c for c in aq))
        out[name] = (tuple(at), tuple(c / n for c in aq), tuple(as_))
    return out


def independent_world(rig_json, local_pose):
    ident = [[1.0 if i == j else 0.0 for j in range(4)] for i in range(4)]
    world = {}
    for joint in rig_json['joints']:
        name = joint['name']
        t, q, s = local_pose[name]
        parent = world.get(joint['parent'], ident)
        world[name] = trs_compose(parent, t, q, s)
    return world


# ----------------------------------------------------------------------
# A. Joint endpoint checks
# ----------------------------------------------------------------------
def make_analytic_rig(path):
    """Tiny 3-joint rig with a closed-form answer for every quantity."""
    def quat(deg, axis, flip=False):
        q = math3d.quat_from_axis_angle(axis, math.radians(deg))
        if flip:
            q = tuple(-c for c in q)
        return [round(c, 10) for c in q]

    rig = {
        'joints': [
            {'name': 'root', 'parent': None, 'position': [0.0, 0.0, 0.0],
             'scale': [2.0, 1.0, 1.0]},
            {'name': 'j1', 'parent': 'root', 'position': [1.0, 0.5, 0.0]},
            {'name': 'j2', 'parent': 'j1', 'position': [0.0, 1.0, 0.0]},
        ],
        'weights': [],
        'actions': {
            'swing': {
                'duration': 1.0, 'loop': False,
                'tracks': {
                    'j1': {'times': [0.0, 1.0],
                           'rotations': [quat(0, (0, 0, 1)),
                                         quat(90, (0, 0, 1), flip=True)]},
                    'j2': {'times': [0.0, 1.0],
                           'rotations': [quat(0, (0, 1, 0), flip=True),
                                         quat(-90, (0, 1, 0))]},
                },
            },
            'stretch': {
                'duration': 1.0, 'loop': False,
                'tracks': {
                    'j1': {'times': [0.0, 1.0],
                           'scales': [[1.0, 1.0, 1.0], [1.0, 2.0, 1.0]]},
                    'j2': {'times': [0.0, 1.0],
                           'translations': [[0.0, 1.0, 0.0],
                                            [0.0, 2.0, 0.5]]},
                },
            },
        },
    }
    with open(path, 'w', encoding='utf-8') as fh:
        json.dump(rig, fh)


def analytic_endpoint(t_swing, t_stretch, w_swing):
    """Closed-form world position of j2 for the analytic rig.

    j1 local: rotation = slerp(Rz(90*t_swing), identity, weights),
    scale = lerp((1,1,1), (1,2,1)) by stretch weight; j2 local
    translation = lerp((0,1,0), (0,2,0.5)).  j2's own rotation does not
    move its origin.  Root applies scale (2,1,1).
    """
    w_s = w_swing
    w_t = 1.0 - w_swing
    ang = math.radians(90.0 * t_swing) * w_s  # nlerp-ish; see note below
    # j1 blended scale and j2 blended translation
    s1 = (1.0, 1.0 + w_t * t_stretch, 1.0)
    t2 = (0.0, 1.0 + w_t * t_stretch, 0.5 * w_t * t_stretch)
    # j2 origin in j1 space: scale applied before rotation (T*R*S)
    p = (s1[0] * t2[0], s1[1] * t2[1], s1[2] * t2[2])
    c, s = math.cos(ang), math.sin(ang)
    p = (c * p[0] - s * p[1], s * p[0] + c * p[1], p[2])
    p = (p[0] + 1.0, p[1] + 0.5, p[2])
    return (2.0 * p[0], p[1], p[2])


def check_endpoints(workdir):
    print('--- A. joint endpoint checks ---')
    failures = []

    rig_path = os.path.join(workdir, 'analytic_rig.json')
    make_analytic_rig(rig_path)
    rig = load_rig(rig_path)

    # Pure swing at t=1: j1 rotates 90 deg about Z.
    pos = rig.joint_world_positions([('swing', 1.0, 1.0)])
    exp = analytic_endpoint(1.0, 0.0, 1.0)
    err = math.dist(pos['j2'], exp)
    print('swing t=1:   got %s expected %s err=%.2e'
          % (_fmt(pos['j2']), _fmt(exp), err))
    if err > 1e-6:
        failures.append('analytic swing endpoint')

    # 50/50 cross-fade between swing@1 and stretch@1.
    pos = rig.joint_world_positions(
        [('swing', 1.0, 0.5), ('stretch', 1.0, 0.5)])
    exp = analytic_endpoint(1.0, 1.0, 0.5)
    err = math.dist(pos['j2'], exp)
    print('blend 50/50: got %s expected %s err=%.2e'
          % (_fmt(pos['j2']), _fmt(exp), err))
    if err > 1e-6:
        failures.append('analytic blend endpoint')

    # Cross-fade continuity: dense sweep, endpoints must move smoothly
    # even though key quaternions are sign-flipped in the file.  A
    # hemisphere flip would teleport the endpoint by O(path length) in
    # a single hop; smooth blending keeps hops within a few average
    # step sizes.
    prev = None
    max_step = 0.0
    path_len = 0.0
    for k in range(81):
        w = k / 80.0
        p = rig.joint_world_positions(
            [('swing', 1.0, 1.0 - w), ('stretch', 1.0, w)])['j2']
        if prev is not None:
            step = math.dist(prev, p)
            max_step = max(max_step, step)
            path_len += step
        prev = p
    avg_step = path_len / 80.0
    print('cross-fade sweep: max endpoint step %.4f vs 3.5x average %.4f'
          % (max_step, 3.5 * avg_step))
    if max_step > 3.5 * avg_step + 1e-9:
        failures.append('cross-fade continuity')

    # Character rig: independent forward kinematics from the JSON file.
    with open(os.path.join(blend_scene.ASSET_DIR, 'character_rig.json'),
              'r', encoding='utf-8') as fh:
        rig_json = json.load(fh)
    char_rig = load_rig(os.path.join(blend_scene.ASSET_DIR,
                                     'character_rig.json'))
    worst = 0.0
    worst_case = None
    for t in (0.0, 0.4, 0.95, 1.3, 1.9, 2.4):
        spec = blend_scene.blend_spec(t)
        got = char_rig.joint_world_positions(spec)
        local = independent_local_pose(rig_json, spec)
        world = independent_world(rig_json, local)
        for name, m in world.items():
            exp = (m[0][3], m[1][3], m[2][3])
            err = math.dist(got[name], exp)
            if err > worst:
                worst = err
                worst_case = (t, name)
    print('character joints: max err over 6 times x %d joints = %.2e %s'
          % (len(world), worst, worst_case))
    if worst > 1e-6:
        failures.append('character joint endpoints')

    print('A: %s' % ('PASS' if not failures else 'FAIL %s' % failures))
    return not failures


def _fmt(p):
    return '(%.4f, %.4f, %.4f)' % p


# ----------------------------------------------------------------------
# B. Skinned vertex checks
# ----------------------------------------------------------------------
def check_vertices():
    print('--- B. skinned vertex checks ---')
    char_obj, _, rig = blend_scene.load_scene()
    with open(os.path.join(blend_scene.ASSET_DIR, 'character_rig.json'),
              'r', encoding='utf-8') as fh:
        rig_json = json.load(fh)

    # Independent skin matrices: own FK + own affine inverse.
    bind_local = {}
    for joint in rig_json['joints']:
        bind_local[joint['name']] = (
            tuple(joint['position']),
            tuple(joint.get('rotation', (0.0, 0.0, 0.0, 1.0))),
            tuple(joint.get('scale', (1.0, 1.0, 1.0))))
    bind_world = independent_world(rig_json, bind_local)
    bind_inv = {n: invert_affine_ind(m) for n, m in bind_world.items()}

    def mul4(a, b):
        return [[sum(a[i][k] * b[k][j] for k in range(4))
                 for j in range(4)] for i in range(4)]

    indices = [0, 25, 51, 77, 103, 129, 155, 181, 207, 233, 259, 287]
    worst = 0.0
    worst_case = None
    for t in (0.0, 0.95, 1.3, 2.4):
        spec = blend_scene.blend_spec(t)
        skinned = rig.skin_vertices_blended(char_obj.vertices, spec)
        local = independent_local_pose(rig_json, spec)
        world = independent_world(rig_json, local)
        skins = {n: mul4(world[n], bind_inv[n]) for n in world}
        for vi in indices:
            vx = char_obj.vertices[vi]
            acc = [0.0, 0.0, 0.0]
            wsum = 0.0
            for name, w in rig_json['weights'][vi]:
                p = mat4_point(skins[name], vx)
                for k in range(3):
                    acc[k] += w * p[k]
                wsum += w
            exp = tuple(c / wsum for c in acc)
            err = math.dist(skinned[vi], exp)
            if err > worst:
                worst = err
                worst_case = (t, vi)
    print('max vertex err over 4 times x %d vertices = %.2e %s'
          % (len(indices), worst, worst_case))
    ok = worst <= 1e-6
    print('B: %s' % ('PASS' if ok else 'FAIL'))
    return ok


# ----------------------------------------------------------------------
# C. Occlusion checks (ray/triangle vs rendered shadow mask)
# ----------------------------------------------------------------------
def _point_segment_dist(p, a, b):
    ab = math3d.sub(b, a)
    denom = math3d.dot(ab, ab)
    if denom == 0.0:
        return math.dist(p, a)
    t = math3d.dot(math3d.sub(p, a), ab) / denom
    t = max(0.0, min(1.0, t))
    closest = (a[0] + t * ab[0], a[1] + t * ab[1], a[2] + t * ab[2])
    return math.dist(p, closest)


def ray_hits_mesh(origin, direction, vertices, faces, margin=0.06,
                  min_separation=0.6, world_margin=0.15):
    """Moeller-Trumbore against every triangle.

    Returns 'hit', 'miss' or 'edge'.  'edge' means the sample is
    unreliable: the ray passes within ``margin`` of a triangle edge (in
    barycentric space) or within ``world_margin`` world units of one
    (shadow-texel sized features cannot be classified stably), or the
    nearest occluder is closer than ``min_separation`` along the ray
    (depth separation too small for a quantized shadow map).
    """
    closest_borderline = False
    hit_t = None
    for face in faces:
        v0 = vertices[face[0][0]]
        v1 = vertices[face[1][0]]
        v2 = vertices[face[2][0]]
        e1 = math3d.sub(v1, v0)
        e2 = math3d.sub(v2, v0)
        p = math3d.cross(direction, e2)
        det = math3d.dot(e1, p)
        if abs(det) < 1e-12:
            continue
        inv = 1.0 / det
        tv = math3d.sub(origin, v0)
        u = math3d.dot(tv, p) * inv
        qv = math3d.cross(tv, e1)
        v = math3d.dot(direction, qv) * inv
        w = 1.0 - u - v
        t = math3d.dot(e2, qv) * inv
        if t <= 0.0:
            continue
        # world-space distance from the plane crossing to each edge
        hit_p = (origin[0] + t * direction[0],
                 origin[1] + t * direction[1],
                 origin[2] + t * direction[2])
        near_edge = min(
            _point_segment_dist(hit_p, v0, v1),
            _point_segment_dist(hit_p, v1, v2),
            _point_segment_dist(hit_p, v2, v0)) < world_margin
        if u >= margin and v >= margin and w >= margin:
            if near_edge:
                closest_borderline = True
            elif hit_t is None or t < hit_t:
                hit_t = t
        elif u > -margin and v > -margin and w > -margin:
            closest_borderline = True
        elif near_edge:
            closest_borderline = True
    if hit_t is not None:
        return 'hit' if hit_t >= min_separation else 'edge'
    return 'edge' if closest_borderline else 'miss'


def check_occlusion():
    print('--- C. occlusion checks (ray cast vs shadow mask) ---')
    char_obj, ground_obj, rig = blend_scene.load_scene()
    renderer = blend_scene.make_renderer()
    to_light = math3d.scale(-1.0, renderer.light_dir)

    frames = (0, 5, 9, 12, 15)
    total = matched = 0
    borderline = 0
    per_frame = []
    for index in frames:
        image, stats = blend_scene.render_frame_at(
            renderer, rig, char_obj, ground_obj, index)
        t = blend_scene.frame_time(index)
        skinned = rig.skin_vertices_blended(char_obj.vertices,
                                            blend_scene.blend_spec(t))
        eye = renderer.eye
        fwd = renderer.forward
        frame_total = frame_matched = 0
        gx = -3.0
        while gx <= 3.0:
            gz = -1.5
            while gz <= 4.0:
                p = (gx, 0.0, gz)
                gz += 0.3
                # project to screen
                clip = math3d.clip_transform(renderer.vp, p)
                if clip[3] <= 0.0:
                    continue
                sx = (clip[0] / clip[3] * 0.5 + 0.5) * renderer.width
                sy = ((1.0 - (clip[1] / clip[3] * 0.5 + 0.5))
                      * renderer.height)
                if not (1.0 <= sx < renderer.width - 1
                        and 1.0 <= sy < renderer.height - 1):
                    continue
                # only test points actually showing ground
                di = int(sy) * renderer.width + int(sx)
                vd = ((p[0] - eye[0]) * fwd[0] + (p[1] - eye[1]) * fwd[1]
                      + (p[2] - eye[2]) * fwd[2])
                if abs(renderer.depth_buf[di] - vd) > 0.15:
                    continue  # pixel shows something closer (character)
                origin = (p[0] + to_light[0] * 0.02,
                          p[1] + to_light[1] * 0.02,
                          p[2] + to_light[2] * 0.02)
                result = ray_hits_mesh(origin, to_light, skinned,
                                       char_obj.faces)
                if result == 'edge':
                    borderline += 1
                    continue
                rendered_lit = renderer.shadow_mask[di] > 128
                expected_lit = result == 'miss'
                frame_total += 1
                if rendered_lit == expected_lit:
                    frame_matched += 1
            gx += 0.3
        total += frame_total
        matched += frame_matched
        per_frame.append((index, frame_matched, frame_total))
        print('frame %02d: occlusion agreement %d/%d'
              % (index, frame_matched, frame_total))
    rate = 100.0 * matched / total if total else 0.0
    print('occlusion consistency: %d/%d = %.2f%% (%d edge samples skipped)'
          % (matched, total, rate, borderline))
    ok = rate >= 95.0
    print('C: %s' % ('PASS' if ok else 'FAIL'))
    return ok


# ----------------------------------------------------------------------
# D. Static-ground stability under sub-texel camera motion
# ----------------------------------------------------------------------
def check_stability():
    print('--- D. static-ground shadow stability (sub-texel camera) ---')
    char_obj, ground_obj, rig = blend_scene.load_scene()
    renderer = blend_scene.make_renderer()
    to_light = math3d.scale(-1.0, renderer.light_dir)

    # Frozen pose after the turn completes.
    t = 1.92
    spec = blend_scene.blend_spec(t)
    skinned = rig.skin_vertices_blended(char_obj.vertices, spec)
    meshes = blend_scene.build_meshes(rig, char_obj, ground_obj, t)

    base_eye, base_target = blend_scene.camera_at(12)
    renderer.set_camera(base_eye, base_target)
    texel = 2.0 * 6.0 / renderer.shadow_size  # conservative; recomputed
    # world units per texel of cascade 0 from its actual projection
    proj = renderer.light_vp[0]
    texel = 2.0 / proj[0] / renderer.shadow_size
    right = math3d.normalize(math3d.cross(renderer.forward, renderer.up))

    # Fixed world ground points, classified once (away from edges).
    samples = []
    gx = -2.4
    while gx <= 2.4:
        gz = -1.2
        while gz <= 3.3:
            p = (gx, 0.0, gz)
            gz += 0.3
            origin = (p[0] + to_light[0] * 0.02,
                      p[1] + to_light[1] * 0.02,
                      p[2] + to_light[2] * 0.02)
            if ray_hits_mesh(origin, to_light, skinned,
                             char_obj.faces) == 'edge':
                continue
            samples.append(p)
        gx += 0.3

    # Render the same pose with sub-texel camera offsets.
    offsets = [math3d.scale(0.30 * k * texel, right) for k in range(4)]
    masks = []
    depths = []
    eyes = []
    for off in offsets:
        eye = math3d.add(base_eye, off)
        renderer.set_camera(eye, base_target)
        renderer.render_shadow(meshes)
        renderer.render_frame(meshes)
        masks.append(bytes(renderer.shadow_mask))
        depths.append(list(renderer.depth_buf))
        eyes.append(eye)

    changed = 0
    max_delta = 0
    used = 0
    for p in samples:
        values = []
        for k in range(4):
            eye = eyes[k]
            renderer.set_camera(eye, base_target)
            clip = math3d.clip_transform(renderer.vp, p)
            if clip[3] <= 0.0:
                break
            sx = int((clip[0] / clip[3] * 0.5 + 0.5) * renderer.width)
            sy = int((1.0 - (clip[1] / clip[3] * 0.5 + 0.5))
                     * renderer.height)
            if not (0 <= sx < renderer.width and 0 <= sy < renderer.height):
                break
            di = sy * renderer.width + sx
            # the pixel must actually show the ground at this point
            vd = ((p[0] - eye[0]) * renderer.forward[0]
                  + (p[1] - eye[1]) * renderer.forward[1]
                  + (p[2] - eye[2]) * renderer.forward[2])
            if abs(depths[k][di] - vd) > 0.15:
                break
            values.append(masks[k][di])
        if len(values) < 4:
            continue
        used += 1
        delta = max(values) - min(values)
        if delta > 0:
            changed += 1
            max_delta = max(max_delta, delta)
    rate = 100.0 * changed / used if used else 0.0
    print('cascade-0 texel = %.5f world units; camera steps of '
          '0.3 texel' % texel)
    print('static ground samples: %d, changed: %d (%.2f%%), '
          'max mask delta: %d' % (used, changed, rate, max_delta))
    ok = rate <= 2.0 and max_delta <= 32
    print('D: %s' % ('PASS' if ok else 'FAIL'))
    return ok


def main():
    workdir = tempfile.mkdtemp(prefix='pipeline_validate_')
    results = [
        check_endpoints(workdir),
        check_vertices(),
        check_occlusion(),
        check_stability(),
    ]
    print('== validation %s ==' % ('PASS' if all(results) else 'FAIL'))
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
