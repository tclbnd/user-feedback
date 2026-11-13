"""Acceptance suite for the per-sample vector rasterizer.

For every scene we compare, bit for bit:
  * the production tiled scanliner (two tile sizes) against each other;
  * the production engine against the independent per-sample winding engine;
  * RGBA against the exact premultiplied formula color * covered/16.

Each scene emits a coverage PGM, a mask XOR diff against the reference, a tile
XOR diff, a white-flattened RGBA PPM and an RGBA diff PPM. A big-coordinate
scene is additionally compared pixel-for-pixel with its small-coordinate twin.
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from rsample.coverage import (composite, popcount, render, rasterize_reference,
                              rasterize_tiled)
from rsample.imageio import (write_coverage_pgm, write_mask_diff_pgm,
                             write_rgba_diff_ppm, write_rgba_ppm)

OUT = Path(__file__).resolve().parent / "output"
TILE_A, TILE_B = 3, 7
COLOR = [0.4, 0.1, 0.3, 0.8]
KAPPA = 0.5522847498307936
BIG = 10_000_003


def circle_cmds(cx, cy, r):
    k = r * KAPPA
    return [
        ["M", cx, cy - r],
        ["C", cx + k, cy - r, cx + r, cy - k, cx + r, cy],
        ["C", cx + r, cy + k, cx + k, cy + r, cx, cy + r],
        ["C", cx - k, cy + r, cx - r, cy + k, cx - r, cy],
        ["C", cx - r, cy - k, cx - k, cy - r, cx, cy - r],
        ["Z"],
    ]


def rect_cmds(x0, y0, x1, y1):
    return [["M", x0, y0], ["L", x1, y0], ["L", x1, y1],
            ["L", x0, y1], ["Z"]]


def scene(size, shape, clips=None, color=None, origin=None):
    data = dict(size=size, shape=shape, color=color or COLOR)
    if clips is not None:
        data["clips"] = clips
    if origin is not None:
        data["origin"] = origin
    return data


def build_scenes():
    scenes = {}

    # 1-2. simple rectangle under both rules; integer bounds exercise the
    # lower-inclusive / upper-exclusive edge convention.
    rect = rect_cmds(1, 1, 5, 4)
    scenes["01_rect_nonzero"] = scene([7, 6], dict(rule="nonzero", commands=rect))
    scenes["02_rect_evenodd"] = scene([7, 6], dict(rule="evenodd", commands=rect))

    # 3-4. self-intersecting bowtie: nonzero keeps the wound-overlap center,
    # evenodd punches it out.
    bow = [["M", 0, 0], ["L", 4, 4], ["L", 4, 0], ["L", 0, 4], ["Z"]]
    scenes["03_bowtie_nonzero"] = scene([5, 5], dict(rule="nonzero", commands=bow))
    scenes["04_bowtie_evenodd"] = scene([5, 5], dict(rule="evenodd", commands=bow))

    # 5. coincident outlines: two squares sharing an exact edge in one path.
    shared = rect_cmds(1, 1, 4, 5) + rect_cmds(4, 1, 7, 5)
    scenes["05_coincident_edge"] = scene([8, 6], dict(rule="evenodd", commands=shared))

    # 6. co-tangent curves: two circles touching at exactly one point.
    tang = circle_cmds(3, 3, 2) + circle_cmds(7, 3, 2)
    scenes["06_tangent_circles"] = scene([10, 6], dict(rule="evenodd", commands=tang))

    # 7. sub-pixel narrow slit: a 0.2 px gap between two filled rectangles must
    # stay transparent on the samples that fall inside it.
    slit = rect_cmds(1, 1, 3.4, 5) + rect_cmds(3.6, 1, 7, 5)
    scenes["07_narrow_slit"] = scene([8, 6], dict(rule="nonzero", commands=slit))

    # 8. one-pixel sliver (0.8 px wide) must not be dropped as a thin edge.
    scenes["08_sliver"] = scene([6, 6],
                                dict(rule="nonzero", commands=rect_cmds(2, 1, 2.8, 5)))

    # 9. nested clips: big shape intersected with a circle and an evenodd ring.
    ring = rect_cmds(0.3, 0.3, 5.7, 5.7) + [["Z"],
                                            ["M", 2, 2], ["L", 4, 2],
                                            ["L", 4, 4], ["L", 2, 4], ["Z"]]
    scenes["09_nested_clips"] = scene(
        [6, 6], dict(rule="nonzero", commands=rect_cmds(0, 0, 6, 6)),
        clips=[dict(rule="nonzero", commands=circle_cmds(3, 3, 2.9)),
               dict(rule="evenodd", commands=ring)])

    # 10. implicit closure: no Z, the subpath closes at end of stream.
    scenes["10_implicit_close"] = scene(
        [6, 5], dict(rule="nonzero",
                     commands=[["M", 1, 1], ["L", 5, 1], ["L", 5, 4], ["L", 1, 4]]))
    return scenes

def build_more(scenes):
    # 11-12. repeated / coincident control points and duplicated contours.
    dup_cmds = [["M", 1, 1], ["L", 1, 1], ["Q", 1, 1, 4, 1],
                ["C", 4, 1, 4, 1, 4, 4], ["L", 1, 4], ["Z"]]
    scenes["11_repeat_controls_nz"] = scene(
        [6, 6], dict(rule="nonzero",
                     commands=dup_cmds + rect_cmds(1, 1, 4, 4)))
    scenes["12_duplicate_evenodd"] = scene(
        [6, 6], dict(rule="evenodd",
                     commands=rect_cmds(1, 1, 4, 4) + rect_cmds(1, 1, 4, 4)))

    # 13. large integer world coordinates translated by origin: must equal the
    # small-coordinate twin rendered without translation.
    big_rect = [["M", BIG + 1, BIG + 1], ["L", BIG + 5, BIG + 1],
                ["L", BIG + 5, BIG + 4], ["L", BIG + 1, BIG + 4], ["Z"]]
    scenes["13_big_coords"] = scene(
        [7, 6], dict(rule="nonzero", commands=big_rect), origin=[BIG, BIG])

    # 14. partial off-screen on every side; also rendered with a tile bigger
    # than the image.
    scenes["14_partial_offscreen"] = scene(
        [6, 5], dict(rule="nonzero", commands=rect_cmds(-3, -2, 9, 8)))

    # 15. zero-area and horizontal-only contours: empty mask, no crash.
    scenes["15_zero_area"] = scene(
        [5, 4], dict(rule="nonzero",
                     commands=[["M", 1, 1], ["L", 4, 1], ["L", 1, 1], ["Z"],
                               ["M", 0, 3], ["L", 5, 3], ["L", 0, 3]]))

    # 16. opposite-winding nested squares: nonzero and evenodd both make an
    # annulus; premultiplied output is checked against the exact formula too.
    outer = rect_cmds(1, 1, 5, 5)
    inner = [["M", 2, 2], ["L", 2, 4], ["L", 4, 4], ["L", 4, 2], ["Z"]]
    scenes["16_annulus_nonzero"] = scene(
        [6, 6], dict(rule="nonzero", commands=outer + inner),
        color=[0.2, 0.7, 0.4, 0.6])

    # 17. same-orientation nested loops make a real winding-2 interior: the
    # evenodd rule punches the inner square out while nonzero keeps it filled.
    # This is the case that actually separates the two fill rules.
    same_orientation = rect_cmds(0, 0, 8, 8) + rect_cmds(2, 2, 6, 6)
    scenes["17_samewind_evenodd"] = scene(
        [8, 8], dict(rule="evenodd", commands=same_orientation))
    scenes["18_samewind_nonzero"] = scene(
        [8, 8], dict(rule="nonzero", commands=same_orientation))
    return scenes


def masks_equal(a, b):
    return all(wa == wb for ra, rb in zip(a, b) for wa, wb in zip(ra, rb))


def rgba_equal(a, b):
    return all(x == y for ra, rb in zip(a, b) for pa, pb in zip(ra, rb)
               for x, y in zip(pa, pb))


def xor_pixels(a, b):
    return sum(popcount(wa ^ wb) for ra, rb in zip(a, b)
               for wa, wb in zip(ra, rb))


def main():
    OUT.mkdir(exist_ok=True)
    scenes = build_more(build_scenes())
    report = dict(scenes={}, failures=[])
    failures = 0

    for name, sc in scenes.items():
        prod_a = render(sc, tile=TILE_A, rasterizer=rasterize_tiled)
        prod_b = render(sc, tile=TILE_B, rasterizer=rasterize_tiled)
        ref = render(sc, tile=TILE_A, rasterizer=rasterize_reference)

        tile_ok = masks_equal(prod_a["mask"], prod_b["mask"]) and \
            rgba_equal(prod_a["rgba"], prod_b["rgba"])
        ref_ok = masks_equal(prod_a["mask"], ref["mask"])
        xor_ref = xor_pixels(prod_a["mask"], ref["mask"])

        formula = composite(prod_a["mask"], sc["color"])
        formula_ok = rgba_equal(prod_a["rgba"], formula)

        big_tile_ok = True
        if name == "14_partial_offscreen":
            prod_huge = render(sc, tile=64, rasterizer=rasterize_tiled)
            big_tile_ok = masks_equal(prod_huge["mask"], prod_a["mask"])

        covered = sum(popcount(w) for row in prod_a["mask"] for w in row)
        ok = tile_ok and ref_ok and formula_ok and big_tile_ok
        if not ok:
            failures += 1
            report["failures"].append(dict(
                name=name, tile_invariant=tile_ok, reference_match=ref_ok,
                premult_formula=formula_ok, big_tile=big_tile_ok,
                xor_ref_samples=xor_ref))

        base = str(OUT / name)
        write_coverage_pgm(base + "_cov.pgm", prod_a["mask"])
        write_mask_diff_pgm(base + "_refdiff.pgm", prod_a["mask"], ref["mask"])
        write_mask_diff_pgm(base + "_tilediff.pgm",
                            prod_a["mask"], prod_b["mask"])
        write_rgba_ppm(base + "_rgb.ppm", prod_a["rgba"])
        write_rgba_diff_ppm(base + "_rgbadiff.ppm",
                            prod_a["rgba"], prod_b["rgba"])

        report["scenes"][name] = dict(
            passed=ok, covered_samples=covered, tile_invariant=tile_ok,
            reference_match=ref_ok, premult_formula=formula_ok,
            big_tile=big_tile_ok, xor_ref_samples=xor_ref)
        print(("PASS" if ok else "FAIL"), name,
              "covered=%d xor_ref=%d" % (covered, xor_ref))

    small = render(scenes["01_rect_nonzero"], tile=TILE_A)
    big = render(scenes["13_big_coords"], tile=TILE_A)
    translate_ok = masks_equal(small["mask"], big["mask"])
    print(("PASS" if translate_ok else "FAIL"), "19_big_origin_equals_small")
    report["scenes"]["19_big_origin_equals_small"] = dict(passed=translate_ok)
    if not translate_ok:
        failures += 1
        report["failures"].append(dict(name="19_big_origin_equals_small"))
    write_mask_diff_pgm(str(OUT / "19_big_origin_diff.pgm"),
                        small["mask"], big["mask"])

    nz = render(scenes["11_repeat_controls_nz"], tile=TILE_A)["mask"]
    eo = render(scenes["12_duplicate_evenodd"], tile=TILE_A)["mask"]
    cancel_ok = all(w == 0 for row in eo for w in row) and \
        any(w == 0xFFFF for row in nz for w in row)
    print(("PASS" if cancel_ok else "FAIL"), "20_evenodd_cancel_nonzero_fill")
    report["scenes"]["20_evenodd_cancel_nonzero_fill"] = dict(passed=cancel_ok)
    if not cancel_ok:
        failures += 1
        report["failures"].append(dict(name="20_evenodd_cancel_nonzero_fill"))

    # The two fill rules must actually separate on a winding-2 interior.
    eo_inner = render(scenes["17_samewind_evenodd"], tile=TILE_A)["mask"]
    nz_inner = render(scenes["18_samewind_nonzero"], tile=TILE_A)["mask"]
    center_hole = eo_inner[4][4] == 0 and nz_inner[4][4] == 0xFFFF
    ring_both = popcount(eo_inner[1][4]) == 16 and \
        popcount(nz_inner[1][4]) == 16
    rule_ok = center_hole and ring_both
    print(("PASS" if rule_ok else "FAIL"), "21_rules_separate_on_winding_two")
    report["scenes"]["21_rules_separate_on_winding_two"] = dict(passed=rule_ok)
    if not rule_ok:
        failures += 1
        report["failures"].append(dict(name="21_rules_separate_on_winding_two"))

    report["summary"] = dict(total=len(report["scenes"]),
                             passed=len(report["scenes"]) - failures,
                             failures=failures)
    (OUT / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n")
    print("\n%d/%d checks passed" % (report["summary"]["passed"],
                                     report["summary"]["total"]))
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
