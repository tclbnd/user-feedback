"""Generate the format-2 humanoid scene assets (stdlib only).

Run once (and re-run after changing the design):

    python3 tools/make_character.py

Writes into assets/:

* character.obj / character.mtl / character_tex.png
* character_rig.json  -- format 2: 11-joint hierarchy, per-vertex
  skin weights and two quaternion clips ("walk", "turn").

The generator is intentionally separate from the runtime: the renderer
only ever reads the generated files, never this script.
"""

import json
import math
import os
import struct
import zlib

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(BASE_DIR, 'assets')


# ----------------------------------------------------------------------
# Skeleton definition (bind pose).  Scale is deliberately non-uniform so
# bind matrices cannot be inverted with the rigid-body inverse.
# ----------------------------------------------------------------------
JOINTS = [
    # name, parent, position, bind rotation (quat), bind scale
    ('root', None, (0.0, 0.0, 0.0), (0, 0, 0, 1), (1.0, 1.0, 1.0)),
    ('pelvis', 'root', (0.0, 0.95, 0.0), (0, 0, 0, 1),
     (1.15, 0.85, 0.90)),
    ('spine', 'pelvis', (0.0, 0.45, 0.0), (0, 0, 0, 1),
     (0.90, 1.20, 0.80)),
    ('head', 'spine', (0.0, 0.62, 0.0), (0, 0, 0, 1),
     (0.85, 0.90, 0.85)),
    ('l_shoulder', 'spine', (0.42, 0.48, 0.0), (0, 0, 0, 1),
     (0.80, 1.00, 0.80)),
    ('r_shoulder', 'spine', (-0.42, 0.48, 0.0), (0, 0, 0, 1),
     (0.80, 1.00, 0.80)),
    ('l_elbow', 'l_shoulder', (0.0, -0.55, 0.0), (0, 0, 0, 1),
     (0.70, 1.10, 0.70)),
    ('r_elbow', 'r_shoulder', (0.0, -0.55, 0.0), (0, 0, 0, 1),
     (0.70, 1.10, 0.70)),
    ('l_knee', 'pelvis', (0.18, -0.50, 0.0), (0, 0, 0, 1),
     (1.05, 0.75, 1.05)),
    ('r_knee', 'pelvis', (-0.18, -0.50, 0.0), (0, 0, 0, 1),
     (1.05, 0.75, 1.05)),
    ('l_foot', 'l_knee', (0.0, -0.48, 0.0), (0, 0, 0, 1),
     (0.90, 0.70, 1.30)),
    ('r_foot', 'r_knee', (0.0, -0.48, 0.0), (0, 0, 0, 1),
     (0.90, 0.70, 1.30)),
]


def joint_world_positions():
    """Bind-pose world joint positions (joint 'position' chains)."""
    pos = {}
    for name, parent, p, _r, _s in JOINTS:
        if parent is None:
            pos[name] = p
        else:
            pp = pos[parent]
            pos[name] = (pp[0] + p[0], pp[1] + p[1], pp[2] + p[2])
    return pos


# ----------------------------------------------------------------------
# Box segment mesh generation
# ----------------------------------------------------------------------
def add_box(verts, uvs, faces, weights, center, size, joint_a,
            joint_b, blend, wp):
    """Append an axis-aligned box; weights blend between two joints.

    Each corner is weighted by ``blend`` toward joint_b (the lower/outer
    endpoint) and ``1-blend`` toward joint_a.  UVs span one full checker
    tile per box face.
    """
    cx, cy, cz = center
    hx, hy, hz = (size[0] * 0.5, size[1] * 0.5, size[2] * 0.5)
    local_corners = [
        (-hx, -hy, -hz), (hx, -hy, -hz), (hx, hy, -hz), (-hx, hy, -hz),
        (-hx, -hy, hz), (hx, -hy, hz), (hx, hy, hz), (-hx, hy, hz)]
    base = len(verts)
    for lc in local_corners:
        verts.append((cx + lc[0], cy + lc[1], cz + lc[2]))
        uvs.append((0.0, 0.0))
        weights.append([[joint_a, 1.0 - blend], [joint_b, blend]])
    # 12 triangles; uv per corner (0/0,1/0,1/1,0/1) per face.
    face_defs = [
        (0, 1, 2, 3),   # -z
        (4, 7, 6, 5),   # +z
        (0, 4, 5, 1),   # -x
        (3, 2, 6, 7),   # +x
        (0, 3, 7, 4),   # -y
        (1, 5, 6, 2),   # +y
    ]
    uv_quads = [(0, 0), (1, 0), (1, 1), (0, 1)]
    for quad in face_defs:
        # give each face its own uv indices so textures map per face
        uv_start = len(uvs)
        for uu, vv in uv_quads:
            uvs.append((float(uu), float(vv)))
        idx = [base + q for q in quad]
        faces.append(((idx[0], uv_start), (idx[1], uv_start + 1),
                      (idx[2], uv_start + 2)))
        faces.append(((idx[0], uv_start), (idx[2], uv_start + 2),
                      (idx[3], uv_start + 3)))


def build_mesh(wp):
    verts, uvs, faces, weights = [], [], [], []
    # torso: pelvis -> spine -> head (single-joint rigid sections)
    add_box(verts, uvs, faces, weights, (0, 0.95, 0), (0.55, 0.45, 0.40),
            'pelvis', 'pelvis', 0.0, wp)
    add_box(verts, uvs, faces, weights, (0, 1.40, 0), (0.48, 0.55, 0.36),
            'spine', 'spine', 0.0, wp)
    add_box(verts, uvs, faces, weights, (0, 1.92, 0), (0.34, 0.40, 0.32),
            'head', 'head', 0.0, wp)
    # upper arms: shoulder center -> elbow, with gentle weight falloff
    for side, sj, ej in (('l', 'l_shoulder', 'l_elbow'),
                         ('r', 'r_shoulder', 'r_elbow')):
        s = wp[sj]
        e = wp[ej]
        mid = ((s[0] + e[0]) / 2, (s[1] + e[1]) / 2, (s[2] + e[2]) / 2)
        add_box(verts, uvs, faces, weights, mid,
                (0.20, 0.62, 0.20), sj, ej, 0.55, wp)
    # thighs / shins / feet
    for side, hip, knee, foot in (
            ('l', 'pelvis', 'l_knee', 'l_foot'),
            ('r', 'pelvis', 'r_knee', 'r_foot')):
        h = wp[hip]
        k = wp[knee]
        f = wp[foot]
        thigh_mid = ((h[0] + k[0]) / 2, (h[1] + k[1]) / 2,
                     (h[2] + k[2]) / 2)
        add_box(verts, uvs, faces, weights, thigh_mid,
                (0.24, 0.56, 0.26), hip, knee, 0.45, wp)
        shin_mid = ((k[0] + f[0]) / 2, (k[1] + f[1]) / 2,
                    (k[2] + f[2]) / 2)
        add_box(verts, uvs, faces, weights, shin_mid,
                (0.20, 0.52, 0.22), knee, foot, 0.5, wp)
        add_box(verts, uvs, faces, weights,
                (f[0], f[1] - 0.06, f[2] + 0.08),
                (0.24, 0.14, 0.42), foot, foot, 0.0, wp)
    return verts, uvs, faces, weights


def write_obj(path, verts, uvs, faces):
    lines = ['mtllib character.mtl', 'o character']
    for v in verts:
        lines.append('v %.5f %.5f %.5f' % v)
    for uv in uvs:
        lines.append('vt %.5f %.5f' % uv)
    lines.append('usemtl character')
    for f in faces:
        lines.append('f %d/%d %d/%d %d/%d' % (
            f[0][0] + 1, f[0][1] + 1,
            f[1][0] + 1, f[1][1] + 1,
            f[2][0] + 1, f[2][1] + 1))
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('\n'.join(lines) + '\n')


def write_mtl(path):
    with open(path, 'w', encoding='utf-8') as fh:
        fh.write('newmtl character\nKd 0.9 0.95 1.0\n'
                 'map_Kd character_tex.png\n')


def write_texture(path):
    """64x64 RGB checker, distinct colors from the floor texture."""
    size = 64
    cells = 8
    cell = size // cells
    data = bytearray(size * size * 3)
    c0 = (70, 110, 170)
    c1 = (210, 180, 120)
    for y in range(size):
        for x in range(size):
            odd = ((x // cell) + (y // cell)) % 2
            r, g, b = c1 if odd else c0
            i = (y * size + x) * 3
            data[i], data[i + 1], data[i + 2] = r, g, b
    raw = bytearray()
    stride = size * 3
    for y in range(size):
        raw.append(0)
        raw += data[y * stride:(y + 1) * stride]
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(ctype, body):
        return (struct.pack('>I', len(body)) + ctype + body +
                struct.pack('>I', zlib.crc32(ctype + body) & 0xFFFFFFFF))

    ihdr = struct.pack('>IIBBBBB', size, size, 8, 2, 0, 0, 0)
    with open(path, 'wb') as fh:
        fh.write(b'\x89PNG\r\n\x1a\n')
        fh.write(chunk(b'IHDR', ihdr))
        fh.write(chunk(b'IDAT', compressed))
        fh.write(chunk(b'IEND', b''))


# ----------------------------------------------------------------------
# Animation clips (quaternion keyframes, hemispheres vary on purpose)
# ----------------------------------------------------------------------
def q_axis_angle(axis, deg):
    a = math.radians(deg)
    s = math.sin(a * 0.5)
    return [axis[0] * s, axis[1] * s, axis[2] * s, math.cos(a * 0.5)]


def flip(q):
    """Return the equivalent -q representation (tests hemisphere logic)."""
    return [-q[0], -q[1], -q[2], -q[3]]


def rotation_track(times, keyframes):
    """keyframes: list of quaternions aligned to times."""
    return {'times': list(times), 'values': keyframes}


def build_walk_clip():
    duration = 2.0
    times = [0.0, 0.25, 0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0]
    joints = {}
    # periodic limb swings
    for jname, phase, axis, amp in (
            ('l_knee', 0.0, (1, 0, 0), 34.0),
            ('r_knee', math.pi, (1, 0, 0), 34.0),
            ('l_elbow', math.pi, (1, 0, 0), 28.0),
            ('r_elbow', 0.0, (1, 0, 0), 28.0),
            ('l_shoulder', math.pi, (1, 0, 0), 20.0),
            ('r_shoulder', 0.0, (1, 0, 0), 20.0)):
        keys = []
        for i, t in enumerate(times):
            ang = amp * math.sin(2 * math.pi * t / duration + phase)
            q = q_axis_angle(axis, ang)
            # deliberately store alternating hemisphere representations
            if i % 2 == 1:
                q = flip(q)
            keys.append(q)
        joints[jname] = {'rotation': rotation_track(times, keys)}
    # subtle spine sway with animated non-uniform scale
    sway = [q_axis_angle((0, 1, 0),
                         6.0 * math.sin(2 * math.pi * t / duration))
            for t in times]
    joints['spine'] = {
        'rotation': rotation_track(times, sway),
        'scale': {'times': times,
                  'values': [[0.90, 1.20 + 0.04 * math.sin(
                      2 * math.pi * t / duration), 0.80]
                             for t in times]}}
    # root walks forward (+z), looping back at the end
    joints['root'] = {
        'translation': {'times': [0.0, 1.0, 2.0],
                        'values': [[0, 0, 0], [0, 0, 1.2], [0, 0, 0]]}}
    return duration, joints


def build_turn_clip():
    duration = 1.6
    times = [0.0, 0.4, 0.8, 1.2, 1.6]
    joints = {}
    # 140-degree turn to the character's right, mostly through the root
    yaw = [0.0, -30.0, -75.0, -120.0, -140.0]
    keys = []
    for i, deg in enumerate(yaw):
        q = q_axis_angle((0, 1, 0), deg)
        if i % 2 == 0:
            q = flip(q)   # mixed equivalent representations
        keys.append(q)
    joints['root'] = {'rotation': rotation_track(times, keys)}
    # counter-sway and arms tucking in while turning
    joints['spine'] = {
        'rotation': rotation_track(
            times, [q_axis_angle((0, 1, 0), -d) for d in yaw]),
        'scale': {'times': times,
                  'values': [[0.90, 1.24, 0.82]] * len(times)}}
    for jname, sign in (('l_shoulder', 1.0), ('r_shoulder', -1.0)):
        joints[jname] = {
            'rotation': rotation_track(
                times, [q_axis_angle((0, 0, 1), sign * 22.0 * t / duration)
                        for t in times])}
    for jname in ('l_elbow', 'r_elbow'):
        joints[jname] = {
            'rotation': rotation_track(
                times, [q_axis_angle((1, 0, 0), 55.0 * t / duration)
                        for t in times])}
    # slight forward lean, knees flexed
    joints['pelvis'] = {
        'rotation': rotation_track(
            times, [q_axis_angle((1, 0, 0), 8.0 * t / duration)
                    for t in times])}
    for jname in ('l_knee', 'r_knee'):
        joints[jname] = {
            'rotation': rotation_track(
                times, [q_axis_angle((1, 0, 0), 20.0 * t / duration)
                        for t in times])}
    return duration, joints


def build_rig(weights):
    walk_duration, walk_joints = build_walk_clip()
    turn_duration, turn_joints = build_turn_clip()
    return {
        'format': 2,
        'name': 'humanoid',
        'joints': [
            {'name': n, 'parent': p, 'position': list(pos),
             'rotation': list(rot), 'scale': list(scl)}
            for (n, p, pos, rot, scl) in JOINTS],
        'weights': weights,
        'clips': {
            'walk': {'duration': walk_duration, 'joints': walk_joints},
            'turn': {'duration': turn_duration, 'joints': turn_joints},
        },
    }


def main():
    os.makedirs(ASSET_DIR, exist_ok=True)
    wp = joint_world_positions()
    verts, uvs, faces, weights = build_mesh(wp)
    write_obj(os.path.join(ASSET_DIR, 'character.obj'), verts, uvs, faces)
    write_mtl(os.path.join(ASSET_DIR, 'character.mtl'))
    write_texture(os.path.join(ASSET_DIR, 'character_tex.png'))
    rig = build_rig(weights)
    with open(os.path.join(ASSET_DIR, 'character_rig.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(rig, fh, indent=1)
    print('wrote character.obj (%d verts, %d tris), character_rig.json '
          '(%d joints, %d clips)'
          % (len(verts), len(faces), len(JOINTS), len(rig['clips'])))


if __name__ == '__main__':
    main()
