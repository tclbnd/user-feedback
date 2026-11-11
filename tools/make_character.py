"""Generate assets/character.obj + assets/character_rig.json (stdlib only).

The character is a 13-joint, 4-level humanoid built from 12 boxes:

    root -> spine -> neck -> head
                 \-> shoulder.L/R -> elbow.L/R
         \-> hip.L/R -> knee.L/R

The spine carries a non-uniform bind scale and the turn action animates
a non-uniform neck scale, so the skinning path must handle general
affine bind matrices.  Two clips are authored:

* ``walk`` (1.0 s, looping): legs swing, knees flex, arms counter-swing,
  root bob.
* ``turn`` (1.2 s, clamped): root yaws 0..180 deg while stepping toward
  +Z, spine follows, neck squashes.

Some key quaternions are deliberately written with flipped sign (q and
-q are the same rotation); consumers must align hemispheres before
blending.  Run:  python3 tools/make_character.py
"""

import json
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                os.pardir))

import math3d  # noqa: E402


ASSET_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         os.pardir, 'assets')


# ----------------------------------------------------------------------
# Skeleton
# ----------------------------------------------------------------------
def build_joints():
    """Return ordered joint dicts: name, parent, local TRS."""
    return [
        ('root',       None,         (0.0, 1.02, 0.0),  None, (1.0, 1.0, 1.0)),
        ('spine',      'root',       (0.0, 0.28, 0.0),  None, (1.0, 1.1, 0.9)),
        ('neck',       'spine',      (0.0, 0.42, 0.0),  None, (1.0, 1.0, 1.0)),
        ('head',       'neck',       (0.0, 0.10, 0.0),  None, (1.0, 1.0, 1.0)),
        ('shoulder.L', 'spine',      (0.24, 0.34, 0.0), None, (1.0, 1.0, 1.0)),
        ('elbow.L',    'shoulder.L', (0.0, -0.30, 0.0), None, (1.0, 1.0, 1.0)),
        ('shoulder.R', 'spine',      (-0.24, 0.34, 0.0), None, (1.0, 1.0, 1.0)),
        ('elbow.R',    'shoulder.R', (0.0, -0.30, 0.0), None, (1.0, 1.0, 1.0)),
        ('hip.L',      'root',       (0.11, -0.06, 0.0), None, (1.0, 1.0, 1.0)),
        ('knee.L',     'hip.L',      (0.0, -0.44, 0.0), None, (1.0, 1.0, 1.0)),
        ('hip.R',      'root',       (-0.11, -0.06, 0.0), None, (1.0, 1.0, 1.0)),
        ('knee.R',     'hip.R',      (0.0, -0.44, 0.0), None, (1.0, 1.0, 1.0)),
    ]


def bind_world_positions(joints):
    """Forward-kinematics over the bind TRS chain -> world positions."""
    world = {}
    mats = {}
    for name, parent, pos, rot, scale in joints:
        q = rot if rot is not None else (0.0, 0.0, 0.0, 1.0)
        local = math3d.trs_matrix(pos, q, scale)
        if parent is None:
            mats[name] = local
        else:
            mats[name] = math3d.mul(mats[parent], local)
        m = mats[name]
        world[name] = (m[3], m[7], m[11])
    return world


# ----------------------------------------------------------------------
# Mesh: one box per body part, 24 verts / 12 tris each (own UVs)
# ----------------------------------------------------------------------
FACE_DEFS = [
    [(1, -1, -1), (1, 1, -1), (1, 1, 1), (1, -1, 1)],        # +X
    [(-1, -1, 1), (-1, 1, 1), (-1, 1, -1), (-1, -1, -1)],    # -X
    [(-1, 1, -1), (-1, 1, 1), (1, 1, 1), (1, 1, -1)],        # +Y
    [(-1, -1, 1), (-1, -1, -1), (1, -1, -1), (1, -1, 1)],    # -Y
    [(-1, -1, 1), (1, -1, 1), (1, 1, 1), (-1, 1, 1)],        # +Z
    [(1, -1, -1), (-1, -1, -1), (-1, 1, -1), (1, 1, -1)],    # -Z
]
FACE_UVS = [(0.0, 0.0), (0.5, 0.0), (0.5, 0.5), (0.0, 0.5)]


def emit_box(vertices, uvs, faces, weights, center, size,
             w_bottom, w_mid, w_top):
    cx, cy, cz = center
    hx, hy, hz = (s * 0.5 for s in size)
    for corners in FACE_DEFS:
        base = len(vertices)
        for corner, uv in zip(corners, FACE_UVS):
            px = cx + corner[0] * hx
            py = cy + corner[1] * hy
            pz = cz + corner[2] * hz
            vertices.append((px, py, pz))
            uvs.append(uv)
            if abs(py - (cy - hy)) < 1e-9:
                weights.append(list(w_bottom))
            elif abs(py - (cy + hy)) < 1e-9:
                weights.append(list(w_top))
            else:
                weights.append(list(w_mid))
        vi = [(base + 0, base + 0), (base + 1, base + 1),
              (base + 2, base + 2), (base + 3, base + 3)]
        faces.append(((vi[0][0], vi[0][0]), (vi[1][0], vi[1][0]),
                      (vi[2][0], vi[2][0])))
        faces.append(((vi[0][0], vi[0][0]), (vi[2][0], vi[2][0]),
                      (vi[3][0], vi[3][0])))


def build_mesh(jpos):
    vertices, uvs, faces, weights = [], [], [], []

    def add(a, b):
        return (a[0] + b[0], a[1] + b[1], a[2] + b[2])

    def mid(a, b):
        return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5,
                (a[2] + b[2]) * 0.5)

    emit_box(vertices, uvs, faces, weights, jpos['root'],
             (0.34, 0.24, 0.22),
             [('root', 1.0)], [('root', 1.0)], [('root', 1.0)])
    emit_box(vertices, uvs, faces, weights,
             mid(jpos['root'], jpos['neck']), (0.38, 0.56, 0.26),
             [('root', 0.5), ('spine', 0.5)],
             [('spine', 1.0)],
             [('spine', 1.0)])
    emit_box(vertices, uvs, faces, weights,
             add(jpos['neck'], (0.0, 0.05, 0.0)), (0.12, 0.24, 0.12),
             [('spine', 0.5), ('neck', 0.5)],
             [('neck', 1.0)],
             [('neck', 1.0)])
    emit_box(vertices, uvs, faces, weights,
             add(jpos['head'], (0.0, 0.15, 0.0)), (0.26, 0.30, 0.26),
             [('neck', 0.4), ('head', 0.6)],
             [('head', 1.0)],
             [('head', 1.0)])
    for side in ('L', 'R'):
        sh = 'shoulder.' + side
        el = 'elbow.' + side
        hip = 'hip.' + side
        knee = 'knee.' + side
        emit_box(vertices, uvs, faces, weights,
                 add(jpos[sh], (0.0, -0.155, 0.0)), (0.13, 0.33, 0.13),
                 [(sh, 1.0)],
                 [(sh, 1.0)],
                 [('spine', 0.35), (sh, 0.65)])
        emit_box(vertices, uvs, faces, weights,
                 add(jpos[el], (0.0, -0.17, 0.0)), (0.11, 0.36, 0.11),
                 [(el, 1.0)],
                 [(el, 1.0)],
                 [(sh, 0.5), (el, 0.5)])
        emit_box(vertices, uvs, faces, weights,
                 add(jpos[hip], (0.0, -0.25, 0.0)), (0.16, 0.52, 0.16),
                 [(hip, 1.0)],
                 [(hip, 1.0)],
                 [('root', 0.5), (hip, 0.5)])
        emit_box(vertices, uvs, faces, weights,
                 add(jpos[knee], (0.0, -0.25, 0.0)), (0.14, 0.52, 0.14),
                 [(knee, 1.0)],
                 [(knee, 1.0)],
                 [(hip, 0.5), (knee, 0.5)])
    return vertices, uvs, faces, weights


# ----------------------------------------------------------------------
# Animation clips
# ----------------------------------------------------------------------
def q(deg, axis):
    return math3d.quat_from_axis_angle(axis, math.radians(deg))


X_AXIS = (1.0, 0.0, 0.0)
Y_AXIS = (0.0, 1.0, 0.0)
Z_AXIS = (0.0, 0.0, 1.0)

# Deterministically flip the sign of some key quaternions: q and -q are
# the same rotation, and the sampler must cope with both.
FLIP_SEED = [False, True, False, False, True, False, True, False]


def quat_list(entries, flip_offset=0):
    out = []
    for i, (deg, axis) in enumerate(entries):
        quat = q(deg, axis)
        if FLIP_SEED[(i + flip_offset) % len(FLIP_SEED)]:
            quat = tuple(-c for c in quat)
        out.append([round(c, 8) for c in quat])
    return out


def build_actions():
    walk_times = [0.0, 0.25, 0.5, 0.75, 1.0]
    walk = {
        'duration': 1.0,
        'loop': True,
        'tracks': {
            'root': {
                'times': walk_times,
                'translations': [
                    [0.0, 1.02, 0.0], [0.0, 1.055, 0.0], [0.0, 1.02, 0.0],
                    [0.0, 1.055, 0.0], [0.0, 1.02, 0.0]],
                'rotations': quat_list(
                    [(3, Z_AXIS), (0, Z_AXIS), (-3, Z_AXIS),
                     (0, Z_AXIS), (3, Z_AXIS)]),
            },
            'spine': {
                'times': walk_times,
                'rotations': quat_list(
                    [(-6, Y_AXIS), (0, Y_AXIS), (6, Y_AXIS),
                     (0, Y_AXIS), (-6, Y_AXIS)], 2),
            },
            'hip.L': {
                'times': walk_times,
                'rotations': quat_list(
                    [(25, X_AXIS), (0, X_AXIS), (-25, X_AXIS),
                     (0, X_AXIS), (25, X_AXIS)], 3),
            },
            'hip.R': {
                'times': walk_times,
                'rotations': quat_list(
                    [(-25, X_AXIS), (0, X_AXIS), (25, X_AXIS),
                     (0, X_AXIS), (-25, X_AXIS)], 4),
            },
            'knee.L': {
                'times': walk_times,
                'rotations': quat_list(
                    [(10, X_AXIS), (5, X_AXIS), (40, X_AXIS),
                     (15, X_AXIS), (10, X_AXIS)], 5),
            },
            'knee.R': {
                'times': walk_times,
                'rotations': quat_list(
                    [(40, X_AXIS), (15, X_AXIS), (10, X_AXIS),
                     (5, X_AXIS), (40, X_AXIS)], 6),
            },
            'shoulder.L': {
                'times': walk_times,
                'rotations': quat_list(
                    [(-20, X_AXIS), (0, X_AXIS), (20, X_AXIS),
                     (0, X_AXIS), (-20, X_AXIS)], 7),
            },
            'shoulder.R': {
                'times': walk_times,
                'rotations': quat_list(
                    [(20, X_AXIS), (0, X_AXIS), (-20, X_AXIS),
                     (0, X_AXIS), (20, X_AXIS)], 1),
            },
            'elbow.L': {
                'times': walk_times,
                'rotations': quat_list(
                    [(-15, X_AXIS), (-5, X_AXIS), (-30, X_AXIS),
                     (-10, X_AXIS), (-15, X_AXIS)], 2),
            },
            'elbow.R': {
                'times': walk_times,
                'rotations': quat_list(
                    [(-30, X_AXIS), (-10, X_AXIS), (-15, X_AXIS),
                     (-5, X_AXIS), (-30, X_AXIS)], 3),
            },
        },
    }

    turn_times = [0.0, 0.3, 0.6, 0.9, 1.2]
    turn = {
        'duration': 1.2,
        'loop': False,
        'tracks': {
            'root': {
                'times': turn_times,
                'translations': [
                    [0.0, 1.02, 0.0], [0.0, 1.02, 0.10],
                    [0.0, 1.02, 0.35], [0.0, 1.02, 0.65],
                    [0.0, 1.02, 0.90]],
                'rotations': quat_list(
                    [(0, Y_AXIS), (45, Y_AXIS), (90, Y_AXIS),
                     (135, Y_AXIS), (180, Y_AXIS)], 4),
            },
            'spine': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, Y_AXIS), (15, Y_AXIS), (25, Y_AXIS),
                     (25, Y_AXIS), (20, Y_AXIS)], 5),
            },
            'neck': {
                'times': turn_times,
                'scales': [
                    [1.0, 1.0, 1.0], [1.15, 0.9, 1.15],
                    [1.25, 0.8, 1.25], [1.15, 0.9, 1.15],
                    [1.0, 1.0, 1.0]],
            },
            'hip.L': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, X_AXIS), (-20, X_AXIS), (10, X_AXIS),
                     (-15, X_AXIS), (0, X_AXIS)], 6),
            },
            'hip.R': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, X_AXIS), (15, X_AXIS), (-10, X_AXIS),
                     (20, X_AXIS), (0, X_AXIS)], 7),
            },
            'knee.L': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, X_AXIS), (30, X_AXIS), (10, X_AXIS),
                     (25, X_AXIS), (5, X_AXIS)], 0),
            },
            'knee.R': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, X_AXIS), (10, X_AXIS), (30, X_AXIS),
                     (10, X_AXIS), (5, X_AXIS)], 1),
            },
            'shoulder.L': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, X_AXIS), (12, X_AXIS), (-8, X_AXIS),
                     (10, X_AXIS), (0, X_AXIS)], 2),
            },
            'shoulder.R': {
                'times': turn_times,
                'rotations': quat_list(
                    [(0, X_AXIS), (-12, X_AXIS), (8, X_AXIS),
                     (-10, X_AXIS), (0, X_AXIS)], 3),
            },
        },
    }
    return {'walk': walk, 'turn': turn}


# ----------------------------------------------------------------------
# Main
# ----------------------------------------------------------------------
def main():
    joints = build_joints()
    jpos = bind_world_positions(joints)
    vertices, uvs, faces, weights = build_mesh(jpos)

    os.makedirs(ASSET_DIR, exist_ok=True)
    obj_path = os.path.join(ASSET_DIR, 'character.obj')
    with open(obj_path, 'w', encoding='utf-8') as fh:
        fh.write('mtllib scene.mtl\no character\n')
        for v in vertices:
            fh.write('v %.5f %.5f %.5f\n' % v)
        for uv in uvs:
            fh.write('vt %.3f %.3f\n' % uv)
        fh.write('usemtl checker\n')
        for face in faces:
            fh.write('f %d/%d %d/%d %d/%d\n' % (
                face[0][0] + 1, face[0][1] + 1,
                face[1][0] + 1, face[1][1] + 1,
                face[2][0] + 1, face[2][1] + 1))

    rig = {
        'joints': [
            {'name': name,
             'parent': parent,
             'position': list(pos),
             **({'rotation': list(rot)} if rot is not None else {}),
             **({'scale': list(scale)} if scale != (1.0, 1.0, 1.0) else {})}
            for name, parent, pos, rot, scale in joints
        ],
        'weights': [
            [[joint, round(w, 6)] for joint, w in vertex_weights]
            for vertex_weights in weights
        ],
        'actions': build_actions(),
    }
    rig_path = os.path.join(ASSET_DIR, 'character_rig.json')
    with open(rig_path, 'w', encoding='utf-8') as fh:
        json.dump(rig, fh, indent=1)

    print('wrote %s (%d vertices, %d faces)'
          % (obj_path, len(vertices), len(faces)))
    print('wrote %s (%d joints, %d weight rows, actions: %s)'
          % (rig_path, len(joints), len(weights),
             ', '.join(rig['actions'])))


if __name__ == '__main__':
    main()
