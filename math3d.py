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
