#!/usr/bin/env python3
"""Stable CSG/SDF ray marching.

The renderer treats a CSG tree as a signed scalar field rather than assuming
that every internal node gives an exact distance to the resulting surface.
Marching therefore uses a conservative local Lipschitz estimate, subdivision
of overshooting steps, and a small forced step near thin walls.

The public tree operations remain the usual SDF CSG rules:
  union = min(a, b), intersection = max(a, b), subtraction = max(a, -b).
All floating-point ties and NaNs are resolved by canonical pre-order primitive
ids, so changing only the scheduler flag ``eval_order`` cannot change output.
"""

from __future__ import annotations

import json
import math
import os
import sys
from typing import Any, Dict, List, Optional, Sequence, Tuple

Vec = List[float]
OWNER_EPS = 1.0e-6
HIT_EPS = 1.0e-4
THIN_DISTANCE = 0.03
THIN_STEP = 0.005
MAX_STEP = 0.25
MAX_T = 20.0
MAX_STEPS = 256
VEC3 = 3


# ---------------------------------------------------------------------------
# Vector helpers
# ---------------------------------------------------------------------------

def vsub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return [a[i] - b[i] for i in range(VEC3)]


def vdot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(a[i] * b[i] for i in range(VEC3))


def vscale(a: Sequence[float], s: float) -> Vec:
    return [s * a[i] for i in range(VEC3)]


def vnorm(a: Sequence[float]) -> float:
    return math.sqrt(vdot(a, a))


def vnormalized(a: Sequence[float]) -> Vec:
    n = vnorm(a)
    if n == 0.0:
        return [0.0, 0.0, 0.0]
    return [a[i] / n for i in range(VEC3)]


def finite_float(x: float) -> bool:
    return not math.isnan(x) and math.isfinite(x)


# ---------------------------------------------------------------------------
# Tree preparation and deterministic CSG comparisons
# ---------------------------------------------------------------------------

def assign_primitive_ids(node: Dict[str, Any], next_id: int = 0) -> int:
    """Assign canonical pre-order ids in the stored tree topology."""
    if "prim" in node:
        node["id"] = next_id
        return next_id + 1
    next_id = assign_primitive_ids(node["a"], next_id)
    return assign_primitive_ids(node["b"], next_id)


def choose_less(da: float, oa: int, db: float, ob: int) -> Tuple[float, int]:
    """Deterministic min; a valid value always beats NaN/non-finite data."""
    a_ok, b_ok = finite_float(da), finite_float(db)
    if not a_ok:
        return db if b_ok else da, ob
    if not b_ok:
        return da, oa
    if da < db or (abs(da - db) <= OWNER_EPS and oa <= ob):
        return da, oa
    return db, ob


def choose_greater(da: float, oa: int, db: float, ob: int) -> Tuple[float, int]:
    """Deterministic max; a valid value always beats NaN/non-finite data."""
    a_ok, b_ok = finite_float(da), finite_float(db)
    if not a_ok:
        return db if b_ok else da, ob
    if not b_ok:
        return da, oa
    if da > db or (abs(da - db) <= OWNER_EPS and oa <= ob):
        return da, oa
    return db, ob


# ---------------------------------------------------------------------------
# Distance, gradient and local Lipschitz information
# ---------------------------------------------------------------------------

def prim_eval(node: Dict[str, Any], p: Sequence[float]) -> Tuple[float, Vec, int]:
    kind = node["prim"]
    owner = node["id"]

    if kind == "sphere":
        c = node["c"]
        q = vsub(p, c)
        r = float(node["r"])
        length = vnorm(q)
        d = length - r
        g = [q[i] / length for i in range(VEC3)] if length > 0.0 else [1.0, 0.0, 0.0]
        return d, g, owner

    if kind == "box":
        c = node["c"]
        h = node["h"]
        q = [abs(p[i] - c[i]) - h[i] for i in range(VEC3)]
        outside = math.sqrt(sum(max(v, 0.0) ** 2 for v in q))
        inside = min(0.0, max(q))
        d = outside + inside

        if outside > 0.0:
            g = [max(q[i], 0.0) / outside for i in range(VEC3)]
        else:
            # Inside the box the signed distance is negative and points toward
            # the nearest face.  At an edge/corner the analytic distance is
            # nonsmooth; selecting the least-penetrating axis gives a
            # deterministic Clarke subgradient.
            axis = max(range(VEC3), key=lambda i: q[i])
            g = [0.0, 0.0, 0.0]
            g[axis] = 1.0 if p[axis] >= c[axis] else -1.0
        return d, g, owner

    if kind == "plane":
        n = vnormalized(node["n"])
        d = vdot(p, n) - float(node["d"])
        return d, n, owner

    raise ValueError(f"unknown primitive: {kind!r}")


def eval_tree(node: Dict[str, Any],
              p: Sequence[float],
              order: str = "post") -> Tuple[float, int, float, Vec]:
    """Evaluate the signed field.

    Returns ``(distance, owner, gradient_norm, gradient)``.  ``order`` selects
    pre-order/post-order traversal only; operand values and canonical tie
    resolution do not depend on it.
    """
    if order not in ("post", "pre"):
        raise ValueError("eval_order must be 'post' or 'pre'")

    if "prim" in node:
        d, g, owner = prim_eval(node, p)
        return d, owner, vnorm(g), g

    if order == "pre":
        # Inspect the operation before its children.  The child values remain
        # in their canonical a/b positions and are never swapped.
        op = node["op"]
        a = eval_tree(node["a"], p, order)
        b = eval_tree(node["b"], p, order)
    else:
        a = eval_tree(node["a"], p, order)
        b = eval_tree(node["b"], p, order)
        op = node["op"]

    da, oa, la, ga = a
    db, ob, lb, gb = b

    if op == "union":
        d, owner = choose_less(da, oa, db, ob)
        g = ga if owner == oa else gb
        lip = la if owner == oa else lb
        return d, owner, lip, g

    if op == "inter":
        d, owner = choose_greater(da, oa, db, ob)
        g = ga if owner == oa else gb
        lip = la if owner == oa else lb
        return d, owner, lip, g

    if op == "sub":
        # max(a, -b).  Ownership keeps the original pre-order id of b.
        neg_db = -db
        neg_gb = vscale(gb, -1.0)
        d, owner = choose_greater(da, oa, neg_db, ob)
        if owner == oa:
            return d, owner, la, ga
        return d, owner, lb, neg_gb

    raise ValueError(f"unknown CSG operation: {op!r}")


# ---------------------------------------------------------------------------
# Ray marching with Lipschitz-limited steps and backtracking
# ---------------------------------------------------------------------------

def point_at(ro: Sequence[float], rd: Sequence[float], t: float) -> Vec:
    return [ro[i] + rd[i] * t for i in range(VEC3)]


def local_ray_lipschitz(tree: Dict[str, Any],
                        ro: Sequence[float],
                        rd: Sequence[float],
                        t: float,
                        d: float,
                        analytic_norm: float,
                        order: str,
                        epsilon: float = 1.0e-5) -> Tuple[float, int]:
    """Estimate a safe one-sided Lipschitz bound along the ray.

    The analytic gradient gives the current directional slope.  A short finite
    difference additionally detects curvature and nonsmooth max(inter/sub)
    CSG kinks whose distance is not globally exact.  Returns the bound and the
    number of auxiliary field evaluations.
    """
    forward_t = min(MAX_T, t + epsilon)
    delta = max(forward_t - t, 1.0e-12)
    forward_d = eval_tree(tree, point_at(ro, rd, forward_t), order)[0]
    if not finite_float(forward_d):
        return max(analytic_norm, 1.0), 1

    finite_difference = abs(forward_d - d) / delta
    return max(analytic_norm, finite_difference, 1.0e-12), 1


def near_thin_wall(d: float) -> bool:
    """Return whether the current point is in the mandatory thin-wall band.

    Once inside this band, all steps are at most ``THIN_STEP``.  This avoids
    relying on the overestimated distance of max(inter/sub) CSG nodes to infer
    wall thickness; the 0.03 band is sampled in at most six 0.005 steps, so a
    0.02-thick wall cannot be skipped.
    """
    return finite_float(d) and abs(d) < THIN_DISTANCE


def march(tree: Dict[str, Any],
          ro: Sequence[float],
          rd: Sequence[float],
          order: str) -> Dict[str, Any]:
    """March a ray and return per-pixel diagnostics and classification."""
    t = 0.0
    steps = 0
    evaluations = 0
    previous_d: Optional[float] = None
    overshoots = 0
    subdivisions = 0

    while t < MAX_T and steps < MAX_STEPS and evaluations < MAX_STEPS:
        p = point_at(ro, rd, t)
        d, owner, lip, gradient = eval_tree(tree, p, order)
        steps += 1
        evaluations += 1

        if not finite_float(d):
            return {
                "hit": False,
                "t": None,
                "steps": evaluations,
                "owner": -1,
                "category": "miss",
                "overshoots": overshoots,
                "subdivisions": subdivisions,
            }

        directional = vdot(gradient, rd)
        if abs(d) <= HIT_EPS:
            category = classify_hit(d, directional, previous_d)
            return {
                "hit": True,
                "t": t,
                "steps": evaluations,
                "owner": owner,
                "category": category,
                "overshoots": overshoots,
                "subdivisions": subdivisions,
            }

        # Conservative local Lipschitz bound along this ray.  A zero or
        # negative directional derivative is common for max-based inter/sub
        # fields and must not lead to an infinite/negative step.
        analytic_norm = max(abs(directional), lip)
        ray_lip, auxiliary_evaluations = local_ray_lipschitz(
            tree, ro, rd, t, d, analytic_norm, order
        )
        evaluations += auxiliary_evaluations
        step = abs(d) / ray_lip
        step = min(step, MAX_STEP)
        if near_thin_wall(d):
            step = min(step, THIN_STEP)
        step = max(step, 1.0e-12)

        old_t = t
        candidate_t = min(MAX_T, t + step)
        candidate_d = eval_tree(tree, point_at(ro, rd, candidate_t), order)[0]
        steps += 1
        evaluations += 1

        # A CSG max field may overestimate distance and jump past a surface.
        # Repeatedly halve such a step instead of accepting the tunnel.
        while (finite_float(candidate_d) and d > 0.0 and candidate_d < 0.0
               and candidate_t - old_t > HIT_EPS):
            overshoots += 1
            subdivisions += 1
            step *= 0.5
            candidate_t = min(MAX_T, old_t + step)
            candidate_d = eval_tree(tree, point_at(ro, rd, candidate_t), order)[0]
            steps += 1
            evaluations += 1
            if step <= THIN_STEP:
                # The forced thin-wall scale is the final subdivision floor.
                break

        previous_d = d
        t = candidate_t
        if t >= MAX_T:
            break

    return {
        "hit": False,
        "t": None,
        "steps": evaluations,
        "owner": -1,
        "category": "miss",
        "overshoots": overshoots,
        "subdivisions": subdivisions,
    }


def classify_hit(d: float, directional: float, previous_d: Optional[float]) -> str:
    """Globally consistent entry/exit rule for a signed field along a ray."""
    if directional < 0.0:
        return "entry"
    if directional > 0.0:
        return "exit"

    # Grazing/nonsmooth case: use the sign transition immediately before hit.
    if previous_d is not None:
        if previous_d > 0.0 and d <= 0.0:
            return "entry"
        if previous_d < 0.0 and d >= 0.0:
            return "exit"
    return "entry"


# ---------------------------------------------------------------------------
# Rendering and output
# ---------------------------------------------------------------------------

def render(sc: Dict[str, Any]) -> Tuple[List[List[int]], List[List[int]], Dict[str, Any]]:
    width, height = sc["res"]
    order = sc.get("eval_order", "post")
    tree = sc["tree"]
    assign_primitive_ids(tree)

    image: List[List[int]] = []
    steps_image: List[List[int]] = []
    pixels: List[Dict[str, Any]] = []
    hits = 0
    entries = 0
    exits = 0
    histogram: Dict[str, int] = {}

    for j in range(height):
        row: List[int] = []
        step_row: List[int] = []
        for i in range(width):
            x = 2.0 * i / width - 1.0
            y = 1.0 - 2.0 * j / height
            rd = vnormalized([x, y, -2.0])
            result = march(tree, [0.0, 0.0, 4.0], rd, order)

            row.append(255 if result["hit"] else 0)
            step_row.append(min(255, result["steps"]))
            if result["hit"]:
                hits += 1
                if result["category"] == "entry":
                    entries += 1
                elif result["category"] == "exit":
                    exits += 1
            bucket = str(result["steps"] // 8)
            histogram[bucket] = histogram.get(bucket, 0) + 1

            pixels.append({
                "i": i,
                "j": j,
                "hit": result["hit"],
                "category": result["category"],
                "owner": result["owner"],
                "which": result["owner"],
                "steps": result["steps"],
                "t": result["t"],
                "overshoots": result["overshoots"],
                "subdivisions": result["subdivisions"],
            })
        image.append(row)
        steps_image.append(step_row)

    stats = {
        "hits": hits,
        "entries": entries,
        "exits": exits,
        "steps_hist": histogram,
        "ownership": [pixel["owner"] for pixel in pixels],
        "pixels": pixels,
    }
    return image, steps_image, stats


def write_pgm(path: str, image: Sequence[Sequence[int]]) -> None:
    height = len(image)
    width = len(image[0]) if height else 0
    with open(path, "w", encoding="ascii") as f:
        f.write(f"P2\n{width} {height}\n255\n")
        f.write("\n".join(" ".join(map(str, row)) for row in image))
        f.write("\n")


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) != 2:
        print("usage: python3 sdf_render.py scene.json outdir", file=sys.stderr)
        return 2

    with open(argv[0], "r", encoding="utf-8") as f:
        scene = json.load(f)
    outdir = argv[1]
    os.makedirs(outdir, exist_ok=True)

    image, steps_image, stats = render(scene)
    write_pgm(os.path.join(outdir, "img.pgm"), image)
    write_pgm(os.path.join(outdir, "steps.pgm"), steps_image)
    with open(os.path.join(outdir, "stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2, sort_keys=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
