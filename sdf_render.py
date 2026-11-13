#!/usr/bin/env python3
"""SDF CSG raymarcher (stable boolean rendering).

Fixes vs the naive baseline:
- deterministic, order-independent CSG combination: `eval_order` only changes
  traversal order, never the result (canonical NaN-safe min/max, fixed operand
  positions, signed-zero canonicalisation).
- overshoot backtracking: inter/sub distances are not exact (they may
  overestimate the true distance), so a step that crosses a surface (d flips
  from positive to negative without a hit) is retried with half the step.
- local Lipschitz step clamp along the ray + forced subdivision (max step
  THIN_STEP) in thin-feature regions (|d| < THIN_EPS).
- per-pixel ownership: pre-order leaf index of the primitive whose surface was
  hit; ties (|d_a - d_b| < TIE_EPS on a shared face) resolve to the earlier
  pre-order leaf -- one global rule everywhere.
- gradient of the CSG tree (central differences) used for a globally
  consistent entry/exit hit classification.

CLI: python3 sdf_render.py scene.json outdir
Outputs: img.pgm, steps.pgm, stats.json
"""
import json, sys, math, os

HIT_EPS   = 1e-4    # hit threshold on |d|
MAX_STEPS = 256
T_MAX     = 20.0
THIN_EPS  = 0.03    # |d| below this -> thin-feature regime
THIN_STEP = 0.005   # forced max step inside the thin regime
TIE_EPS   = 1e-6    # ownership tie tolerance on shared faces
FD_H      = 1e-3    # finite-difference step for the ray-direction Lipschitz estimate
GRAD_H    = 1e-5    # finite-difference step for the gradient


# ---------------------------------------------------------------- primitives
def prim_eval(node, p):
    k = node["prim"]
    if k == "sphere":
        c = node["c"]
        return math.dist(p, c) - node["r"]
    if k == "box":
        c, h = node["c"], node["h"]
        q = [abs(p[i] - c[i]) - h[i] for i in range(3)]
        o = math.sqrt(sum(v * v for v in q if v > 0))
        return o + min(0.0, max(q))
    if k == "plane":
        n = node["n"]
        return sum(p[i] * n[i] for i in range(3)) - node["d"]
    raise ValueError("unknown prim %r" % k)


# ------------------------------------------------------------------ CSG eval
def _canon(v):
    # canonicalise -0.0 -> +0.0 so equal-valued results are bit-identical
    # regardless of operand order
    return v + 0.0

def _csg_min(a, b):
    # order-independent, NaN-safe min: a NaN operand always loses
    if a != a:
        return _canon(b)
    if b != b:
        return _canon(a)
    return _canon(a if a < b else b)

def _csg_max(a, b):
    if a != a:
        return _canon(b)
    if b != b:
        return _canon(a)
    return _canon(a if a > b else b)

def eval_tree(node, p, order="post"):
    """CSG distance at p. `order` ("pre"/"post") only changes the traversal
    order of the subtrees; the returned value is bit-identical either way."""
    if "prim" in node:
        return prim_eval(node, p)
    a, b = node["a"], node["b"]
    if order == "pre":
        da = eval_tree(a, p, order)
        db = eval_tree(b, p, order)
    else:  # post: evaluate the b subtree first
        db = eval_tree(b, p, order)
        da = eval_tree(a, p, order)
    op = node["op"]
    if op == "union":
        return _csg_min(da, db)
    if op == "inter":
        return _csg_max(da, db)
    if op == "sub":
        return _csg_max(da, -db)
    raise ValueError("unknown op %r" % op)

def grad_tree(node, p, order="post", h=GRAD_H):
    """Gradient of the CSG distance via central differences."""
    g = []
    for ax in range(3):
        pp = list(p)
        pm = list(p)
        pp[ax] += h
        pm[ax] -= h
        g.append((eval_tree(node, pp, order) - eval_tree(node, pm, order)) / (2.0 * h))
    return g


# ----------------------------------------------------------------- ownership
def leaves_preorder(node, out):
    if "prim" in node:
        out.append(node)
    else:
        leaves_preorder(node["a"], out)
        leaves_preorder(node["b"], out)
    return out

def owner_at(tree, p):
    """Pre-order index of the leaf closest to its own surface at p.
    On shared faces (|d_a - d_b| < TIE_EPS) the earlier pre-order leaf wins --
    a single global rule, independent of evaluation order."""
    best_i, best_a = -1, float("inf")
    for i, leaf in enumerate(leaves_preorder(tree, [])):
        ad = abs(prim_eval(leaf, p))
        if ad < best_a - TIE_EPS:
            best_a, best_i = ad, i
    return best_i


# ------------------------------------------------------------------ marching
def march(tree, ro, rd, order="post"):
    """Sphere tracing with Lipschitz clamping, thin-feature subdivision and
    overshoot backtracking. Returns (t, steps, hit)."""
    def at(t):
        return [ro[i] + rd[i] * t for i in range(3)]

    t, steps = 0.0, 0
    d = eval_tree(tree, at(0.0), order)
    while t < T_MAX and steps < MAX_STEPS:
        if d < HIT_EPS:
            return t, steps, True
        # local Lipschitz estimate along the ray: step <= d / L
        dl = eval_tree(tree, at(t + FD_H), order)
        L = abs(dl - d) / FD_H
        step = d / L if L > 1.0 else d
        # thin-feature regime: force subdivision so thin walls cannot be
        # jumped over by a single step
        if abs(d) < THIN_EPS and step > THIN_STEP:
            step = THIN_STEP
        # overshoot backtracking: inter/sub distances may overestimate the
        # true distance, so a step can cross a surface without a hit
        dn = d
        while steps < MAX_STEPS:
            dn = eval_tree(tree, at(t + step), order)
            steps += 1
            if dn < -HIT_EPS and step > 1e-6:
                step *= 0.5
                continue
            break
        t += step
        d = dn
    return (t if d < HIT_EPS else None), steps, d < HIT_EPS


# ------------------------------------------------------------------- render
def render(sc):
    W, H = sc["res"]
    order = sc.get("eval_order", "post")
    img, steps_img = [], []
    ownership, classes = [], []
    hits = 0
    hist = {}
    for j in range(H):
        row, srow = [], []
        for i in range(W):
            x = 2 * i / W - 1
            y = 1 - 2 * j / H
            rd = [x, y, -2.0]
            n = math.sqrt(sum(v * v for v in rd))
            rd = [v / n for v in rd]
            t, s, hit = march(sc["tree"], [0, 0, 4], rd, order)
            row.append(255 if hit else 0)
            srow.append(min(255, s))
            if hit:
                hits += 1
                p = [rd[0] * t, rd[1] * t, 4.0 + rd[2] * t]
                ownership.append(owner_at(sc["tree"], p))
                # entry/exit: globally consistent rule via gradient . rd
                g = grad_tree(sc["tree"], p, order)
                sgn = g[0] * rd[0] + g[1] * rd[1] + g[2] * rd[2]
                classes.append(1 if sgn < 0 else 2)  # 1=entry, 2=exit
            else:
                ownership.append(-1)
                classes.append(0)
            hist[s // 8] = hist.get(s // 8, 0) + 1
        img.append(row)
        steps_img.append(srow)
    stats = {"hits": hits, "steps_hist": hist,
             "ownership": ownership, "class": classes}
    return img, steps_img, stats


def write_pgm(p, img):
    h, w = len(img), len(img[0])
    open(p, "w").write(f"P2\n{w} {h}\n255\n" +
                       "\n".join(" ".join(map(str, r)) for r in img))


def main():
    sc = json.load(open(sys.argv[1]))
    out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    img, s, stats = render(sc)
    write_pgm(os.path.join(out, "img.pgm"), img)
    write_pgm(os.path.join(out, "steps.pgm"), s)
    json.dump(stats, open(os.path.join(out, "stats.json"), "w"))


if __name__ == "__main__":
    main()
