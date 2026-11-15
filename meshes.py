"""Test mesh generators (used by the acceptance harness)."""
import math


def grid_disk(n=6):
    """Planar unit-square grid, strict disk topology."""
    pos = []
    index = {}

    def vid(i, j):
        k = (i, j)
        if k not in index:
            index[k] = len(pos)
            pos.append((i / n, j / n, 0.0))
        return index[k]

    tris = []
    for i in range(n):
        for j in range(n):
            a = vid(i, j)
            b = vid(i + 1, j)
            c = vid(i + 1, j + 1)
            d = vid(i, j + 1)
            tris.append((a, b, c))
            tris.append((a, c, d))
    return pos, tris


def cylinder(nt=8, nz=4, r=1.0, h=2.0, caps=False):
    """Cylinder tube: annulus (genus 0, 2 boundary loops) or capped disk."""
    pos = []
    for j in range(nz + 1):
        z = h * j / nz
        for i in range(nt):
            th = 2 * math.pi * i / nt
            pos.append((r * math.cos(th), r * math.sin(th), z))

    def idx(i, j):
        return j * nt + (i % nt)

    tris = []
    for j in range(nz):
        for i in range(nt):
            a = idx(i, j)
            b = idx(i + 1, j)
            c = idx(i + 1, j + 1)
            d = idx(i, j + 1)
            tris.append((a, b, c))
            tris.append((a, c, d))
    return pos, tris


def torus(nu=12, nv=8, R=2.0, r=0.8):
    """Closed torus, genus 1, no boundary."""
    pos = []

    def idx(i, j):
        return (j % nv) * nu + (i % nu)

    for j in range(nv):
        for i in range(nu):
            u = 2 * math.pi * i / nu
            v = 2 * math.pi * j / nv
            pos.append((
                (R + r * math.cos(v)) * math.cos(u),
                (R + r * math.cos(v)) * math.sin(u),
                r * math.sin(v),
            ))
    tris = []
    for j in range(nv):
        for i in range(nu):
            a = idx(i, j)
            b = idx(i + 1, j)
            c = idx(i + 1, j + 1)
            d = idx(i, j + 1)
            tris.append((a, b, c))
            tris.append((a, c, d))
    return pos, tris


def two_tori(nu=8, nv=6, R=2.0, r=0.7):
    """Genus-2 connected sum of two tori, offset so they do not overlap."""
    a = torus(nu, nv, R, r)
    b = torus(nu, nv, R, r)
    # shift the second torus far away so vertex welding cannot merge them
    shifted = [(x, y + 10.0, z) for (x, y, z) in b[0]]
    return connect_sum(a, (shifted, b[1]))


def connect_sum(m1, m2):
    """Cut one face from each mesh and glue the two triangular holes."""
    p1, t1 = m1
    p2, t2 = m2
    t1 = [list(t) for t in t1]
    t2 = [list(t) for t in t2]
    f1 = t1.pop()
    f2 = t2.pop()
    off = len(p1)
    pos = list(p1) + list(p2)
    b0, b1, b2 = (off + x for x in f2)
    a0, a1, a2 = f1
    remap = {b0: a0, b2: a1, b1: a2}
    tris = [tuple(t) for t in t1]
    for t in t2:
        tris.append(tuple(remap.get(off + x, off + x) for x in t))
    used = sorted({v for t in tris for v in t})
    mp = {v: i for i, v in enumerate(used)}
    return [pos[v] for v in used], [tuple(mp[v] for v in t) for t in tris]


def with_degenerate(m):
    """Append a zero-area triangle and a repeated-index triangle."""
    pos, tris = m
    pos = list(pos)
    tris = [tuple(t) for t in tris]
    n = len(pos)
    pos.append(pos[0])
    tris.append((0, n, 1))
    tris.append((2, 2, 3))
    return pos, tris


def permute(m, seed=0):
    """Randomly permute vertex order and triangle order and cyclically rotate
    the vertices inside each triangle, plus reverse some orientations."""
    import random
    rng = random.Random(seed)
    pos, tris = m
    order = list(range(len(pos)))
    rng.shuffle(order)
    newpos = [None] * len(pos)
    for old, new in enumerate(order):
        newpos[new] = pos[old]
    out = []
    for t in tris:
        a, b, c = (order[v] for v in t)
        r = rng.randrange(3)
        if r == 1:
            a, b, c = b, c, a
        elif r == 2:
            a, b, c = c, a, b
        out.append((a, b, c))
    rng.shuffle(out)
    return newpos, out
