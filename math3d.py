"""Row-major 4x4 matrix / vector math (stdlib only).

Vectors are plain tuples/lists of floats.  Matrices are flat 16-element
lists in row-major order: element (row r, col c) is m[r*4 + c].
Point/vector transforms use column vectors, i.e. v' = M * v.
"""

import math


def identity():
    return [1.0, 0.0, 0.0, 0.0,
            0.0, 1.0, 0.0, 0.0,
            0.0, 0.0, 1.0, 0.0,
            0.0, 0.0, 0.0, 1.0]


def mul(a, b):
    """Return a * b (apply b first, then a)."""
    out = [0.0] * 16
    for r in range(4):
        ar = r * 4
        for c in range(4):
            s = 0.0
            for k in range(4):
                s += a[ar + k] * b[k * 4 + c]
            out[ar + c] = s
    return out


def transform(m, v):
    """Transform a 3D point (implicit w=1), return 3-tuple."""
    x, y, z = v
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11])


def clip_transform(m, v):
    """Transform a 3D point, return full clip coords (x, y, z, w)."""
    x, y, z = v
    return (m[0] * x + m[1] * y + m[2] * z + m[3],
            m[4] * x + m[5] * y + m[6] * z + m[7],
            m[8] * x + m[9] * y + m[10] * z + m[11],
            m[12] * x + m[13] * y + m[14] * z + m[15])


def translation(x, y, z):
    return [1.0, 0.0, 0.0, float(x),
            0.0, 1.0, 0.0, float(y),
            0.0, 0.0, 1.0, float(z),
            0.0, 0.0, 0.0, 1.0]


def rotation_axis(axis, angle_rad):
    """Rodrigues rotation matrix around a unit axis."""
    ax, ay, az = axis
    n = math.sqrt(ax * ax + ay * ay + az * az)
    ax, ay, az = ax / n, ay / n, az / n
    c = math.cos(angle_rad)
    s = math.sin(angle_rad)
    t = 1.0 - c
    return [t * ax * ax + c,       t * ax * ay - s * az,   t * ax * az + s * ay,   0.0,
            t * ax * ay + s * az,  t * ay * ay + c,        t * ay * az - s * ax,   0.0,
            t * ax * az - s * ay,  t * ay * az + s * ax,   t * az * az + c,        0.0,
            0.0,                   0.0,                    0.0,                    1.0]



def perspective(fovy_rad, aspect, near, far):
    """OpenGL-style perspective projection; NDC z maps to [0, 1]."""
    f = 1.0 / math.tan(fovy_rad / 2.0)
    return [f / aspect, 0.0, 0.0, 0.0,
            0.0, f, 0.0, 0.0,
            0.0, 0.0, (far + near) / (near - far), (2.0 * far * near) / (near - far),
            0.0, 0.0, -1.0, 0.0]


def ortho(left, right, bottom, top, near, far):
    """Orthographic projection; NDC z maps to [0, 1]."""
    return [2.0 / (right - left), 0.0, 0.0, -(right + left) / (right - left),
            0.0, 2.0 / (top - bottom), 0.0, -(top + bottom) / (top - bottom),
            0.0, 0.0, -1.0 / (far - near), -near / (far - near),
            0.0, 0.0, 0.0, 1.0]


def look_at(eye, target, up):
    """Right-handed view matrix: camera looks down -Z."""
    ex, ey, ez = eye
    fx = target[0] - ex
    fy = target[1] - ey
    fz = target[2] - ez
    ln = math.sqrt(fx * fx + fy * fy + fz * fz)
    fx, fy, fz = fx / ln, fy / ln, fz / ln
    # side = normalize(cross(forward, up))
    sx = fy * up[2] - fz * up[1]
    sy = fz * up[0] - fx * up[2]
    sz = fx * up[1] - fy * up[0]
    sn = math.sqrt(sx * sx + sy * sy + sz * sz)
    sx, sy, sz = sx / sn, sy / sn, sz / sn
    # true up = cross(side, forward)
    ux = sy * fz - sz * fy
    uy = sz * fx - sx * fz
    uz = sx * fy - sy * fx
    return [sx, sy, sz, -(sx * ex + sy * ey + sz * ez),
            ux, uy, uz, -(ux * ex + uy * ey + uz * ez),
            -fx, -fy, -fz, fx * ex + fy * ey + fz * ez,
            0.0, 0.0, 0.0, 1.0]


def sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def scale(s, v):
    return (s * v[0], s * v[1], s * v[2])


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def normalize(v):
    ln = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if ln == 0.0:
        return (0.0, 0.0, 0.0)
    return (v[0] / ln, v[1] / ln, v[2] / ln)


# ----------------------------------------------------------------------
# General 4x4 matrix inverse (handles non-uniform scale, unlike the
# rigid-body inverse in skinning.py).
# ----------------------------------------------------------------------
def inverse(m):
    """Inverse of an arbitrary affine/perspective 4x4 matrix (Gauss-Jordan)."""
    a = list(m)
    inv = [0.0] * 16
    # augment with identity
    aug = [0.0] * 32
    for r in range(4):
        for c in range(4):
            aug[r * 8 + c] = a[r * 4 + c]
        aug[r * 8 + 4 + r] = 1.0
    for col in range(4):
        pivot = col
        best = abs(aug[col * 8 + col])
        for r in range(col + 1, 4):
            val = abs(aug[r * 8 + col])
            if val > best:
                best = val
                pivot = r
        if best < 1e-15:
            raise ValueError('matrix is singular and cannot be inverted')
        if pivot != col:
            base_c, base_p = col * 8, pivot * 8
            for k in range(8):
                aug[base_c + k], aug[base_p + k] = \
                    aug[base_p + k], aug[base_c + k]
        piv_row = col * 8
        piv_val = aug[piv_row + col]
        for k in range(8):
            aug[piv_row + k] /= piv_val
        for r in range(4):
            if r == col:
                continue
            factor = aug[r * 8 + col]
            if factor == 0.0:
                continue
            row_b = r * 8
            for k in range(8):
                aug[row_b + k] -= factor * aug[piv_row + k]
    for r in range(4):
        for c in range(4):
            inv[r * 4 + c] = aug[r * 8 + 4 + c]
    return inv


def compose(t, r, s):
    """Compose TRS: T(t) * R(quaternion r) * S(s).

    ``t`` is a 3-tuple translation, ``r`` a unit quaternion (x, y, z, w),
    ``s`` a 3-tuple scale (may be non-uniform).
    """
    x, y, z, w = r
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    sx, sy, sz = s
    return [
        (1.0 - 2.0 * (yy + zz)) * sx, 2.0 * (xy - wz) * sx,
        2.0 * (xz + wy) * sx, t[0],
        2.0 * (xy + wz) * sy, (1.0 - 2.0 * (xx + zz)) * sy,
        2.0 * (yz - wx) * sy, t[1],
        2.0 * (xz - wy) * sz, 2.0 * (yz + wx) * sz,
        (1.0 - 2.0 * (xx + yy)) * sz, t[2],
        0.0, 0.0, 0.0, 1.0]


def quat_identity():
    return (0.0, 0.0, 0.0, 1.0)


def quat_from_axis_angle(axis, angle_rad):
    ax, ay, az = normalize(axis)
    half = 0.5 * angle_rad
    s = math.sin(half)
    return (ax * s, ay * s, az * s, math.cos(half))


def quat_normalize(q):
    x, y, z, w = q
    n = math.sqrt(x * x + y * y + z * z + w * w)
    if n == 0.0:
        return (0.0, 0.0, 0.0, 1.0)
    return (x / n, y / n, z / n, w / n)


def quat_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def quat_canonical(q):
    """Map a quaternion to its canonical hemisphere (w >= 0).

    q and -q describe the same rotation; blending must compare them on a
    single hemisphere to avoid spinning the long way around.
    """
    if q[3] < 0.0:
        return (-q[0], -q[1], -q[2], -q[3])
    return q


def quat_mul(a, b):
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def quat_slerp(q0, q1, t):
    """Spherical linear interpolation with hemisphere + short-arc handling."""
    q0 = quat_normalize(q0)
    q1 = quat_canonical(quat_normalize(q1))
    d = max(-1.0, min(1.0, quat_dot(q0, q1)))
    if d > 0.9995:
        # nearly identical: linear blend + renormalize is stable and cheap
        out = (q0[k] + t * (q1[k] - q0[k]) for k in range(4))
        return quat_normalize(tuple(out))
    theta_0 = math.acos(d)
    theta = theta_0 * t
    sin_theta_0 = math.sin(theta_0)
    s0 = math.sin(theta_0 - theta) / sin_theta_0
    s1 = math.sin(theta) / sin_theta_0
    return quat_normalize(tuple(
        s0 * q0[k] + s1 * q1[k] for k in range(4)))


def quat_lerp(q0, q1, t):
    """Canonicalized normalized linear blend (NLERP)."""
    q1 = quat_canonical(q1)
    return quat_normalize(tuple(
        q0[k] + t * (q1[k] - q0[k]) for k in range(4)))


def quat_to_matrix(q):
    return compose((0.0, 0.0, 0.0), q, (1.0, 1.0, 1.0))


def lerp(a, b, t):
    return a + (b - a) * t


def vlerp(a, b, t):
    return tuple(a[k] + (b[k] - a[k]) * t for k in range(len(a)))


def vscale(v, s):
    return tuple(v[k] * s[k] for k in range(len(v)))


# ----------------------------------------------------------------------
# Ray / triangle intersection (Moller-Trumbore), used by the occlusion
# verification tests rather than the rasterizer.
# ----------------------------------------------------------------------
def ray_triangle(origin, direction, a, b, c, eps=1e-9):
    """Return (t, u, v) of the hit or None. ``direction`` need not be unit."""
    e1 = sub(b, a)
    e2 = sub(c, a)
    p = cross(direction, e2)
    det = dot(e1, p)
    if abs(det) < eps:
        return None
    inv_det = 1.0 / det
    tv = sub(origin, a)
    u = dot(tv, p) * inv_det
    if u < -eps or u > 1.0 + eps:
        return None
    q = cross(tv, e1)
    v = dot(direction, q) * inv_det
    if v < -eps or u + v > 1.0 + eps:
        return None
    t = dot(e2, q) * inv_det
    if t < eps:
        return None
    return t, u, v


def ray_mesh_any_hit(origin, direction, vertices, faces, max_t, eps=1e-9):
    """True if any triangle is hit strictly before ``max_t``."""
    for face in faces:
        a = vertices[face[0][0]]
        b = vertices[face[1][0]]
        c = vertices[face[2][0]]
        hit = ray_triangle(origin, direction, a, b, c, eps)
        if hit is not None and hit[0] < max_t:
            return True
    return False
