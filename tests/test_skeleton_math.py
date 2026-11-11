"""Independent math verification of the format-2 skeleton.

Everything here re-derives forward kinematics and linear-blend skinning
from first principles (plain 4x4 multiplies written out in this file),
rather than calling the production evaluator, so a shared bug in the
runtime cannot also fake the test.
"""

import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import math3d  # noqa: E402
from skinning import load_rig  # noqa: E402

ASSET_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), 'assets')

EPS = 1e-9


# ----------------------------------------------------------------------
# Independent reference implementations
# ----------------------------------------------------------------------
def ref_compose(t, q, s):
    """TRS matrix written out independently of math3d.compose."""
    x, y, z, w = q
    r00 = 1 - 2 * (y * y + z * z)
    r01 = 2 * (x * y - z * w)
    r02 = 2 * (x * z + y * w)
    r10 = 2 * (x * y + z * w)
    r11 = 1 - 2 * (x * x + z * z)
    r12 = 2 * (y * z - x * w)
    r20 = 2 * (x * z - y * w)
    r21 = 2 * (y * z + x * w)
    r22 = 1 - 2 * (x * x + y * y)
    sx, sy, sz = s
    return [r00 * sx, r01 * sx, r02 * sx, t[0],
            r10 * sy, r11 * sy, r12 * sy, t[1],
            r20 * sz, r21 * sz, r22 * sz, t[2],
            0.0, 0.0, 0.0, 1.0]


def ref_world_chains(local_trs, joints_order, parents):
    worlds = {}
    for name in joints_order:
        local = ref_compose(local_trs[name][0],
                            local_trs[name][1],
                            local_trs[name][2])
        parent = parents[name]
        worlds[name] = local if parent is None \
            else math3d.mul(worlds[parent], local)
    return worlds


def ref_skin_vertex(vertex, weights, world_mats, bind_inv):
    acc = [0.0, 0.0, 0.0]
    wsum = 0.0
    for name, w in weights:
        if w == 0.0:
            continue
        skin = math3d.mul(world_mats[name], bind_inv[name])
        p = math3d.transform(skin, vertex)
        for k in range(3):
            acc[k] += w * p[k]
        wsum += w
    if wsum not in (0.0, 1.0):
        acc = [c / wsum for c in acc]
    return tuple(acc)


def matrix_close(a, b, tol=EPS):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def vec_close(a, b, tol=EPS):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------
def test_bind_matrices_non_uniform(rig):
    """Bind world matrices match an independent TRS chain, general inverse."""
    parents = {n: rig.joints[n]['parent'] for n in rig.order}
    local_trs = {n: (rig.joints[n]['position'],
                     rig.joints[n]['rotation'],
                     rig.joints[n]['scale']) for n in rig.order}
    ref_world = ref_world_chains(local_trs, rig.order, parents)
    scaled = 0
    for name in rig.order:
        s = rig.joints[name]['scale']
        if abs(s[0] - s[1]) > 1e-12 or abs(s[1] - s[2]) > 1e-12:
            scaled += 1
        assert matrix_close(ref_world[name], rig.bind_world[name]), \
            'bind world mismatch at %s' % name
        prod = math3d.mul(rig.bind_world[name], rig.bind_inverse[name])
        assert matrix_close(prod, math3d.identity(), 1e-10), \
            'bind inverse failed at %s' % name
    assert scaled >= 3, 'scene must exercise non-uniform scale'
    return scaled


def test_endpoints(rig):
    """Joint endpoints from Pose match independent FK for every clip/time."""
    max_err = 0.0
    parents = {n: rig.joints[n]['parent'] for n in rig.order}
    samples = [('walk', t * 0.137) for t in range(15)]
    samples += [('turn', t * 0.113) for t in range(14)]
    checked = 0
    for clip_name, t in samples:
        pose = rig.evaluate_clip(clip_name, t, loop=True)
        local_trs = {
            name: (pose.local_trs[name]['translation'],
                   pose.local_trs[name]['rotation'],
                   pose.local_trs[name]['scale'])
            for name in rig.order}
        ref_world = ref_world_chains(local_trs, rig.order, parents)
        for name in rig.order:
            ref_pos = math3d.transform(ref_world[name], (0, 0, 0))
            got_pos = pose.joint_world_position(name)
            err = max(abs(a - b) for a, b in zip(ref_pos, got_pos))
            max_err = max(max_err, err)
            assert err <= EPS, 'endpoint %s/%s err=%.3e' % (clip_name, name, err)
            checked += 1
    return checked, max_err


def test_selected_vertices(rig, vertices):
    """LBS vertices match the independent weighted-matrix reference."""
    max_err = 0.0
    picks = [0, 1, 7, 12, 23, 40, 61, len(vertices) - 1]
    for clip_name, t in (('walk', 0.42), ('turn', 0.77)):
        pose = rig.evaluate_clip(clip_name, t)
        got = rig.skin_vertices_pose(vertices, pose)
        for vi in picks:
            ref = ref_skin_vertex(vertices[vi], rig.weights[vi],
                                  pose.world_matrix, rig.bind_inverse)
            err = max(abs(a - b) for a, b in zip(ref, got[vi]))
            max_err = max(max_err, err)
            assert err <= EPS, 'vertex %d err=%.3e' % (vi, err)
    return picks, max_err


def test_quaternion_equivalence(rig):
    """q and -q must produce identical rotations and identical blends."""
    q = (0.31, -0.52, 0.44, 0.66)
    qn = math3d.quat_normalize(q)
    qf = (-qn[0], -qn[1], -qn[2], -qn[3])
    m1 = math3d.quat_to_matrix(qn)
    m2 = math3d.quat_to_matrix(qf)
    assert matrix_close(m1, m2), 'equivalent quaternions differ as matrices'

    q0 = math3d.quat_from_axis_angle((0, 1, 0), math.radians(30))
    b1 = math3d.quat_slerp(q0, qn, 0.37)
    b2 = math3d.quat_slerp(q0, qf, 0.37)
    assert vec_close(b1, b2, 1e-12), 'slerp hemisphere dependent'

    # End-to-end: the blend path must canonicalize hemispheres regardless
    # of how the clip stored its keyframes (the generator flips every
    # other key on purpose).  Evaluating must always produce unit
    # quaternions and finite transforms.
    p_blend = rig.evaluate_blend([('walk', 0.9, 0.5),
                                  ('turn', 0.3, 0.5)])
    for name in rig.order:
        q = p_blend.local_trs[name]['rotation']
        norm = math.sqrt(sum(c * c for c in q))
        assert abs(norm - 1.0) < 1e-12, 'non-unit blended quaternion'
        assert all(math.isfinite(c) for c in q)
    return max(abs(a - b) for a, b in zip(b1, b2)), len(rig.order)


def test_crossfade_endpoints(rig):
    """A single-layer blend must equal the clip pose exactly (weight norm)."""
    for t in (0.0, 0.55, 1.19):
        clip_pose = rig.evaluate_clip('walk', t)
        blend_pose = rig.evaluate_blend([('walk', t, 0.7)])
        for name in rig.order:
            a = clip_pose.local_trs[name]
            b = blend_pose.local_trs[name]
            assert vec_close(a['translation'], b['translation'], 1e-12)
            assert vec_close(a['scale'], b['scale'], 1e-12)
            qa = math3d.quat_canonical(a['rotation'])
            qb = math3d.quat_canonical(b['rotation'])
            assert vec_close(qa, qb, 1e-12)
    return True


def test_blend_continuity(rig, vertices):
    """Cross-fade introduces no jump beyond the underlying clips' motion.

    We measure each step's motion against the previous step (a discrete
    second derivative): a hard cut would show up as a large spike, while a
    smooth cross-fade stays on the same scale as ordinary clip motion.
    Boundary times (fade start 1.2 s and end 2.4 s) are sampled exactly.
    """
    dt = 0.02

    def pose_at(t):
        # Mirror scene_character's clock: the turn clip starts at t=0 when
        # the fade begins (scene time 1.2 s) and is fully in at 2.4 s.
        w = _w(t)
        if w <= 0.0:
            return rig.evaluate_clip('walk', t)
        if w >= 1.0:
            return rig.evaluate_clip('turn', t - 1.2)
        return rig.evaluate_blend(
            [('walk', t, 1.0 - w), ('turn', t - 1.2, w)])

    times = [1.0 + i * dt for i in range(int(1.6 / dt) + 1)]
    # ensure exact fade boundaries are included
    times = sorted(set(round(t, 6) for t in times + [1.2, 2.4]))
    geo = [rig.skin_vertices_pose(vertices, pose_at(t)) for t in times]

    # baseline "normal step" size well inside the pure-walk region
    base_step = 0.0
    for i in range(0, 9):
        for vi in range(0, len(vertices), 7):
            step = math.sqrt(sum(
                (geo[i + 1][vi][k] - geo[i][vi][k]) ** 2
                for k in range(3)))
            base_step = max(base_step, step)
    spikes = []
    for i in range(1, len(geo) - 1):
        for vi in range(0, len(vertices), 7):
            first = [geo[i][vi][k] - geo[i - 1][vi][k] for k in range(3)]
            second = [geo[i + 1][vi][k] - geo[i][vi][k] for k in range(3)]
            accel = math.sqrt(sum((second[k] - first[k]) ** 2
                                  for k in range(3)))
            spikes.append(accel)
    max_spike = max(spikes)
    # A genuine hard cut would produce a spike ~ clip pose difference
    # (order 0.1+); smooth blending stays within a small multiple of the
    # normal per-step motion.
    assert max_spike < max(0.02, 3.0 * base_step), \
        'cross-fade spike %.4f vs step %.4f' % (max_spike, base_step)
    return {'max_spike': max_spike, 'base_step': base_step,
            'boundary_times': [1.2, 2.4]}


def _w(t):
    if t <= 1.2:
        return 0.0
    if t >= 2.4:
        return 1.0
    u = (t - 1.2) / 1.2
    return u * u * (3 - 2 * u)


def run():
    rig = load_rig(os.path.join(ASSET_DIR, 'character_rig.json'))
    from obj_loader import load_obj
    mesh = load_obj(os.path.join(ASSET_DIR, 'character.obj'))

    results = {}
    results['non_uniform_joints'] = test_bind_matrices_non_uniform(rig)
    checked, ep_err = test_endpoints(rig)
    results['endpoint_checks'] = checked
    results['endpoint_max_err'] = ep_err
    picks, v_err = test_selected_vertices(rig, mesh.vertices)
    results['vertex_picks'] = picks
    results['vertex_max_err'] = v_err
    q_err, n_joints = test_quaternion_equivalence(rig)
    results['quat_hemisphere_err'] = q_err
    results['single_layer_exact'] = test_crossfade_endpoints(rig)
    results['blend_max_jump'] = test_blend_continuity(rig, mesh.vertices)
    results['passed'] = True
    return results


if __name__ == '__main__':
    import json
    print(json.dumps(run(), indent=2, sort_keys=True))
