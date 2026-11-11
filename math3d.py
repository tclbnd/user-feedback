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
# Quaternions: stored as (x, y, z, w) tuples.
# ----------------------------------------------------------------------

def quat_identity():
    return (0.0, 0.0, 0.0, 1.0)


def quat_from_axis_angle(axis, angle_rad):
    ax, ay, az = normalize(axis)
    h = angle_rad * 0.5
    s = math.sin(h)
    return (ax * s, ay * s, az * s, math.cos(h))


def quat_normalize(q):
    ln = math.sqrt(q[0] * q[0] + q[1] * q[1] + q[2] * q[2] + q[3] * q[3])
    if ln == 0.0:
        return quat_identity()
    return (q[0] / ln, q[1] / ln, q[2] / ln, q[3] / ln)


def quat_mul(a, b):
    """Hamilton product a * b (apply b's rotation first, then a's)."""
    ax, ay, az, aw = a
    bx, by, bz, bw = b
    return (aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw,
            aw * bw - ax * bx - ay * by - az * bz)


def quat_dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2] + a[3] * b[3]


def quat_align(a, b):
    """Flip ``a`` if needed so it lies in the same hemisphere as ``b``.

    q and -q encode the same rotation; interpolating between opposite
    hemispheres takes the long way around (or collapses), so callers must
    align signs before blending.
    """
    if quat_dot(a, b) < 0.0:
        return (-a[0], -a[1], -a[2], -a[3])
    return a


def quat_slerp(a, b, t):
    """Shortest-path interpolation; handles the q / -q equivalence."""
    b = quat_align(b, a)
    d = quat_dot(a, b)
    if d > 0.9995:
        return quat_normalize(tuple(a[i] + t * (b[i] - a[i])
                                    for i in range(4)))
    d = max(-1.0, min(1.0, d))
    theta = math.acos(d)
    sin_t = math.sin(theta)
    wa = math.sin((1.0 - t) * theta) / sin_t
    wb = math.sin(t * theta) / sin_t
    return tuple(wa * a[i] + wb * b[i] for i in range(4))


def quat_to_matrix3(q):
    """Return the 3x3 rotation (flat 9, row-major) of a unit quaternion."""
    x, y, z, w = quat_normalize(q)
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return [1.0 - 2.0 * (yy + zz), 2.0 * (xy - wz), 2.0 * (xz + wy),
            2.0 * (xy + wz), 1.0 - 2.0 * (xx + zz), 2.0 * (yz - wx),
            2.0 * (xz - wy), 2.0 * (yz + wx), 1.0 - 2.0 * (xx + yy)]


def trs_matrix(translation_v, quat, scale_v):
    """Compose T * R * S as a row-major 4x4 (column-vector convention)."""
    r = quat_to_matrix3(quat)
    sx, sy, sz = scale_v
    tx, ty, tz = translation_v
    return [r[0] * sx, r[1] * sy, r[2] * sz, tx,
            r[3] * sx, r[4] * sy, r[5] * sz, ty,
            r[6] * sx, r[7] * sy, r[8] * sz, tz,
            0.0, 0.0, 0.0, 1.0]


def invert_affine(m):
    """Inverse of a general affine 4x4 (rotation/scale/shear + translation).

    Unlike invert_rigid this tolerates non-uniform scale, which skinned
    bind matrices require once joints carry per-axis scaling.
    """
    a, b, c, tx = m[0], m[1], m[2], m[3]
    d, e, f, ty = m[4], m[5], m[6], m[7]
    g, h, i, tz = m[8], m[9], m[10], m[11]
    det = (a * (e * i - f * h)
           - b * (d * i - f * g)
           + c * (d * h - e * g))
    if abs(det) < 1e-12:
        raise ValueError('singular affine matrix')
    inv = 1.0 / det
    r00 = (e * i - f * h) * inv
    r01 = (c * h - b * i) * inv
    r02 = (b * f - c * e) * inv
    r10 = (f * g - d * i) * inv
    r11 = (a * i - c * g) * inv
    r12 = (c * d - a * f) * inv
    r20 = (d * h - e * g) * inv
    r21 = (b * g - a * h) * inv
    r22 = (a * e - b * d) * inv
    return [r00, r01, r02, -(r00 * tx + r01 * ty + r02 * tz),
            r10, r11, r12, -(r10 * tx + r11 * ty + r12 * tz),
            r20, r21, r22, -(r20 * tx + r21 * ty + r22 * tz),
            0.0, 0.0, 0.0, 1.0]
