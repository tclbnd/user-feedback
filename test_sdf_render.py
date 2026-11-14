#!/usr/bin/env python3
"""Acceptance-style checks for sdf_render.py (standard library only)."""
import copy
import json
import math
import os
import random
import tempfile
import unittest

import sdf_render as s


def sphere(c=(0, 0, 0), r=1.0):
    return dict(prim="sphere", c=list(c), r=r)


def box(c=(0, 0, 0), h=(0.5, 0.5, 0.5)):
    return dict(prim="box", c=list(c), h=list(h))


def image_from_stats(stats, width, height):
    return [1 if p["hit"] else 0 for p in stats["pixels"]]


def contour_pixels(stats):
    hit = set((p["i"], p["j"]) for p in stats["pixels"] if p["hit"])
    border = set()
    for i, j in hit:
        for di, dj in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            if (i + di, j + dj) not in hit:
                border.add((i, j))
                break
    return border


def directed_hausdorff(a, b):
    if not a:
        return 0.0
    return max(min(math.hypot(x - u, y - v) for u, v in b) for x, y in a)


def hausdorff(a, b):
    return max(directed_hausdorff(a, b), directed_hausdorff(b, a))


def project_sphere_mesh_outline(radius, width, height):
    """Rasterize the analytic silhouette used by a dense sphere mesh."""
    cx = (width - 1) / 2.0
    cy = (height - 1) / 2.0
    # The fixed camera ray at NDC (x,y) has direction (x,y,-2).  Its closest
    # approach to the sphere center is the projected silhouette radius.
    ndc_radius = 2.0 * radius / math.sqrt(16.0 - radius * radius)
    pixel_radius = ndc_radius * width / 2.0
    pixels = set()
    for j in range(height):
        for i in range(width):
            if math.hypot(i - cx, j - cy) <= pixel_radius:
                pixels.add((i, j))
    return contour_pixels(dict(pixels=[
        dict(i=i, j=j, hit=(i, j) in pixels)
        for j in range(height) for i in range(width)
    ]))


class SDFRenderTests(unittest.TestCase):
    def test_primitive_matches_analytic_solution(self):
        tree = sphere(r=0.8)
        s.assign_primitive_ids(tree)
        points = ((0, 0, 0), (0.8, 0, 0), (1.2, -0.3, 0.4), (-2, 1, 5))
        for p in points:
            for p in points:
                got, owner, lip, grad = s.eval_tree(tree, p, "post")
                pre_got, pre_owner, pre_lip, pre_grad = s.eval_tree(tree, p, "pre")
                want = math.sqrt(sum(v * v for v in p)) - 0.8
                self.assertEqual(owner, 0)
                self.assertTrue(math.isclose(got, want, rel_tol=1e-14, abs_tol=1e-14))
                self.assertTrue(math.isclose(lip, 1.0, abs_tol=1e-14))
                self.assertEqual((got, owner, lip, grad), (pre_got, pre_owner, pre_lip, pre_grad))

    def test_single_box_and_plane_match_analytic_solution(self):
        box_tree = box(c=(1, 0, -1), h=(0.5, 0.25, 0.75))
        s.assign_primitive_ids(box_tree)
        got, owner, _, _ = s.eval_tree(box_tree, (1.7, 0.4, -1.1), "post")
        # Outside components are (0.2, 0.15, 0); distance = hypot(0.2, 0.15).
        self.assertEqual(owner, 0)
        self.assertTrue(math.isclose(got, 0.25, abs_tol=1e-14))

        plane_tree = dict(prim="plane", n=(0, 0, 2), d=1.0)
        s.assign_primitive_ids(plane_tree)
        got, owner, lip, gradient = s.eval_tree(plane_tree, (1, 2, 3), "post")
        self.assertEqual(owner, 0)
        self.assertTrue(math.isclose(got, 2.0, abs_tol=1e-14))
        self.assertTrue(math.isclose(lip, 1.0, abs_tol=1e-14))
        self.assertEqual(gradient, [0, 0, 1])

    def test_box_gradient_is_inward_inside(self):
        tree = box(h=(1, 1, 0.01))
        s.assign_primitive_ids(tree)
        _, _, _, grad = s.eval_tree(tree, (0, 0, 0), "post")
        self.assertEqual(grad, [0, 0, 1])

    def test_eval_order_is_bit_for_bit_stable(self):
        for op in ("union", "inter", "sub"):
            tree = dict(op=op, a=sphere(), b=box(c=(0.1, 0, 0)))
            s.assign_primitive_ids(tree)
            for p in ((0, 0, 0), (0.2, -0.3, 0.7), (-1, 2, 3)):
                self.assertEqual(
                    s.eval_tree(copy.deepcopy(tree), p, "post"),
                    s.eval_tree(copy.deepcopy(tree), p, "pre"),
                )

    def test_render_outputs_are_order_independent(self):
        scene = dict(
            res=(32, 32),
            eval_order="post",
            tree=dict(op="sub", a=sphere(), b=box(c=(0.1, 0, 0))),
        )
        with tempfile.TemporaryDirectory() as root:
            path = os.path.join(root, "scene.json")
            with open(path, "w") as f:
                json.dump(scene, f)
            self.assertEqual(s.main([path, os.path.join(root, "post")]), 0)
            scene["eval_order"] = "pre"
            with open(path, "w") as f:
                json.dump(scene, f)
            self.assertEqual(s.main([path, os.path.join(root, "pre")]), 0)
            for name in ("img.pgm", "steps.pgm", "stats.json"):
                with open(os.path.join(root, "post", name), "rb") as f:
                    a = f.read()
                with open(os.path.join(root, "pre", name), "rb") as f:
                    b = f.read()
                self.assertEqual(a, b)

    def test_thin_0_02_wall_is_not_tunneled(self):
        tree = dict(
            op="inter",
            a=box(h=(1, 1, 0.01)),
            b=box(h=(0.9, 0.9, 0.02)),
        )
        s.assign_primitive_ids(tree)
        result = s.march(tree, (0, 0, 4), (0, 0, -1), "post")
        self.assertTrue(result["hit"])
        self.assertEqual(result["category"], "entry")
        self.assertLess(abs(result["t"] - 3.99), 1e-4)

    def test_boolean_outline_against_direct_ray_reference(self):
        width = height = 64
        # A large front box intersects exactly the front cap of the sphere.
        # Its analytic ray/boolean reference has the same silhouette as the
        # sphere while still exercising a non-exact max(intersection) node.
        tree = dict(op="inter", a=sphere(r=0.9), b=box(c=(0, 0, -0.2), h=(4, 4, 1.0)))
        scene = dict(res=(width, height), eval_order="post", tree=tree)
        _, _, stats = s.render(scene)
        rendered = contour_pixels(stats)

        reference_mask = set()
        for j in range(height):
            y = 1 - 2 * j / height
            for i in range(width):
                x = 2 * i / width - 1
                a = x * x + y * y + 4.0
                # Unnormalized ray quadratic for origin (0,0,4), then convert
                # to the normalized ray parameter used by the renderer.
                discriminant = 256.0 - 4.0 * a * 15.19
                if discriminant >= 0.0:
                    reference_mask.add((i, j))

        reference = contour_pixels(dict(pixels=[
            dict(i=i, j=j, hit=(i, j) in reference_mask)
            for j in range(height) for i in range(width)
        ]))
        self.assertLessEqual(hausdorff(rendered, reference), 1.0)

    def test_outline_matches_projected_analytic_mesh(self):
        width = height = 64
        radius = 0.9
        tree = dict(op="inter", a=sphere(r=radius), b=box(c=(0, 0, -0.2), h=(4, 4, 1.0)))
        scene = dict(res=(width, height), eval_order="post", tree=tree)
        _, _, stats = s.render(scene)
        rendered = contour_pixels(stats)
        mesh_outline = project_sphere_mesh_outline(radius, width, height)
        self.assertTrue(mesh_outline)
        self.assertLessEqual(hausdorff(rendered, mesh_outline), 1.0)

    def test_random_eval_scheduling_does_not_change_summary(self):
        base = dict(
            op="union",
            a=dict(op="inter", a=sphere(r=0.7), b=box(h=(0.9, 0.9, 0.9))),
            b=dict(op="sub", a=sphere(c=(0.3, 0, 0), r=0.6), b=box(c=(0.5, 0, 0))),
        )
        summaries = []
        for seed in range(5):
            rng = random.Random(seed)
            scene = dict(res=(24, 24), eval_order=rng.choice(("post", "pre")), tree=copy.deepcopy(base))
            _, _, stats = s.render(scene)
            summaries.append((
                stats["hits"],
                stats["entries"],
                stats["exits"],
                tuple(sorted(stats["steps_hist"].items())),
                tuple(p["hit"] for p in stats["pixels"]),
                tuple(p["category"] for p in stats["pixels"]),
                tuple(p["steps"] for p in stats["pixels"]),
            ))
        self.assertEqual(len(set(summaries)), 1)

        # Reversing all commutative children and reassigning canonical ids
        # changes ownership ids by construction, but must not change the hit
        # mask, step counts, or entry/exit categories.
        def reverse(node):
            result = copy.deepcopy(node)
            if "prim" in result:
                return result
            result["a"] = reverse(result["a"])
            result["b"] = reverse(result["b"])
            if result["op"] in ("union", "inter"):
                result["a"], result["b"] = result["b"], result["a"]
            return result

        _, _, original = s.render(dict(res=(24, 24), eval_order="post", tree=copy.deepcopy(base)))
        reversed_tree = reverse(base)
        _, _, reversed_stats = s.render(dict(res=(24, 24), eval_order="post", tree=reversed_tree))
        for key in ("hit", "category", "steps"):
            self.assertEqual(
                [p[key] for p in original["pixels"]],
                [p[key] for p in reversed_stats["pixels"]],
            )

    def test_common_face_ownership_uses_canonical_preorder(self):
        # Two planes share the face z=0.  The one first in tree pre-order owns
        # it even though floating-point distances differ by less than 1e-6.
        tree = dict(
            op="union",
            a=dict(prim="plane", n=(0, 0, 1), d=0.0),
            b=dict(prim="plane", n=(0, 0, 1), d=1.0e-9),
        )
        s.assign_primitive_ids(tree)
        d, owner, _, _ = s.eval_tree(tree, (0, 0, 0), "post")
        self.assertEqual(owner, 0)
        self.assertTrue(math.isclose(d, 0.0, abs_tol=1e-12))

        swapped = dict(
            op="union",
            a=dict(prim="plane", n=(0, 0, 1), d=1.0e-9),
            b=dict(prim="plane", n=(0, 0, 1), d=0.0),
        )
        s.assign_primitive_ids(swapped)
        _, swapped_owner, _, _ = s.eval_tree(swapped, (0, 0, 0), "post")
        self.assertEqual(swapped_owner, 0)

    def test_stats_contain_required_per_pixel_information(self):
        scene = dict(res=(4, 4), eval_order="post", tree=sphere())
        _, _, stats = s.render(scene)
        self.assertEqual(len(stats["pixels"]), 16)
        self.assertEqual(len(stats["ownership"]), 16)
        self.assertIn("steps_hist", stats)
        for pixel in stats["pixels"]:
            self.assertIn("steps", pixel)
            self.assertIn("category", pixel)
            self.assertIn("owner", pixel)
            self.assertIn("which", pixel)
            self.assertEqual(pixel["owner"], pixel["which"])
            self.assertIn(pixel["category"], ("entry", "exit", "miss"))


if __name__ == "__main__":
    unittest.main()
