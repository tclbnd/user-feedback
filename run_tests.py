#!/usr/bin/env python3
"""Acceptance tests for the chart-isolated lightmap baker.

A1  Interleaved constant bright/dark charts: every supported mip level,
    every valid sample point.  Dark charts must show no foreign colour
    above 1e-5 per linear channel; bright interiors must stay constant
    within 1e-5 after dilation.
A2  Sloped mesh with mirrored UVs and duplicated seam vertices compared
    against a high-resolution reference (irradiance and seam deltas),
    over five seeded random packings; canonical summaries reached via
    chart inverse mapping must be identical.
A3  Order independence: shuffled chart/triangle order and reordered
    atlas writes give identical per-chart arrays.
A4  demo.json contract smoke test and rotation round trip.
"""
import json
from pathlib import Path

from lightmap import bake
from lightmap.packer import pack_random, LCG
from lightmap.mesh import build_slope
from lightmap.sample import sample_chart, sample_atlas, invert_atlas_texel
from lightmap.atlas import rotate_pixel, inverse_pixel

TOL = 1e-5
failures = []
notes = []


def check(cond, msg):
    if not cond:
        failures.append(msg)
    return cond


def full_cover_chart(cid, n, ambient, normal):
    t1 = {"uv": [[0, 0], [1, 0], [1, 1]], "normal": [normal] * 3}
    t2 = {"uv": [[0, 1], [1, 1], [0, 0]], "normal": [normal] * 3}
    return {"id": cid, "size": n, "ambient": ambient,
            "rotate": 0, "triangles": [t1, t2]}


def test_bright_dark():
    # Interleave bright and dark charts in one atlas row.
    charts = []
    n = 16
    for k in range(8):
        bright = (k % 2 == 0)
        cid = ("B%d" if bright else "D%d") % k
        charts.append(full_cover_chart(
            cid, n, ambient=1.0 if bright else 0.0,
            normal=[0, 0, 1] if bright else [0, 0, -1]))
    # Force explicit interleaved origins and alternating rotations.
    for k, c in enumerate(charts):
        c["origin"] = [k * n, 0]
        c["rotate"] = (k * 90) % 360
    data = {"charts": charts, "light": [0, 0, 1], "strength": 0.8,
            "padding": 2, "levels": 5}
    res = bake(data)
    levels = res["params"]["levels"]
    check(levels == 5, "A1: five levels supported (got %d)" % levels)

    # Irradiance is ambient + max(0, dot(n, light)) * strength, so the
    # bright charts are constant 1.8 and dark charts stay exactly 0.
    bright_const, dark_const = 1.0 + 0.8, 0.0
    for c in charts:
        cid = c["id"]
        bright = cid.startswith("B")
        const = bright_const if bright else dark_const
        chain = res["charts"][cid]["levels"]
        for li, entry in enumerate(chain):
            vals = [v for v in entry["light"] if v is not None]
            check(len(vals) == entry["size"] ** 2,
                  "A1: %s level %d fully owned after dilation" % (cid, li))
            # Every valid sample point at this level.
            worst = max(abs(v - const) for v in vals)
            check(worst <= TOL,
                  "A1: %s level %d constant drift %.3e > %.0e"
                  % (cid, li, worst, TOL))
            if not bright:
                leak = max(vals)
                check(leak <= TOL,
                      "A1: dark %s level %d foreign colour %.3e"
                      % (cid, li, leak))

    # Atlas-level guarantee: no dark atlas texel may carry bright light,
    # and conflict records must never hide an averaged value.
    for li, lvl in enumerate(res["atlas"]):
        for k, owner in enumerate(lvl["owners"]):
            if owner is None:
                continue
            v = lvl["light"][k]
            if str(owner).startswith("D"):
                check(v <= TOL,
                      "A1: atlas level %d dark texel leaked %.3e" % (li, v))
        for cf in lvl["conflicts"]:
            check("averag" not in cf.get("reason", ""),
                  "A1: conflicts must not be blended")
    notes.append("A1 bright/dark: %d charts x %d levels, worst drift within %.0e"
                 % (len(charts), levels, TOL))


def test_partial_bright_dilation():
    # A bright chart covering only the inner half and an adjacent dark
    # chart: dilation/fill must propagate the bright constant to every
    # edge texel without importing any dark value.
    bright = {
        "id": "PB", "size": 16, "ambient": 1.0, "origin": [0, 0],
        "rotate": 90,
        "triangles": [
            {"uv": [[0.25, 0.25], [0.75, 0.25], [0.75, 0.75]],
             "normal": [[0, 0, 1]] * 3},
            {"uv": [[0.25, 0.75], [0.75, 0.75], [0.25, 0.25]],
             "normal": [[0, 0, 1]] * 3},
        ],
    }
    dark = full_cover_chart("PD", 16, 0.0, [0, 0, -1])
    dark["origin"] = [16, 0]
    data = {"charts": [bright, dark], "light": [0, 0, 1], "strength": 0.8,
            "padding": 4, "levels": 5}
    res = bake(data)
    const = 1.8
    for li, entry in enumerate(res["charts"]["PB"]["levels"]):
        vals = [v for v in entry["light"] if v is not None]
        check(len(vals) == entry["size"] ** 2,
              "A1b: partial bright level %d fully filled" % li)
        worst = max(abs(v - const) for v in vals)
        check(worst <= TOL,
              "A1b: partial bright level %d edge/interior drift %.3e"
              % (li, worst))
        # Explicit canonical sample points including border UVs.
        for u in (0.0, 0.25, 0.5, 0.75, 1.0):
            for v in (0.0, 0.25, 0.5, 0.75, 1.0):
                got = sample_chart(entry, u, v)
                check(abs(got - const) <= TOL,
                      "A1b: border sample (%g,%g) drifted" % (u, v))
    for li, entry in enumerate(res["charts"]["PD"]["levels"]):
        check(max(entry["light"]) <= TOL,
              "A1b: dark neighbour contaminated at level %d" % li)
    notes.append("A1b partial bright: edge and interior constant within %.0e "
                 "after isolated dilation" % TOL)


def _summary(res):
    """Packing-independent canonical summary (per chart, per level)."""
    out = {}
    for cid, ch in sorted(res["charts"].items()):
        rows = []
        for entry in ch["levels"]:
            vals = [None if v is None else round(v, 9) for v in entry["light"]]
            owners = list(entry["owners"])
            rows.append((entry["size"], vals, owners))
        out[cid] = rows
    return out


def _reference_irradiance(mesh, light, strength):
    """High-resolution analytic reference on the slope's smooth normal."""
    n = mesh["smooth_normal"]
    d = sum(n[i] * light[i] for i in range(3))
    return max(0.0, d) * strength


def test_slope_packings():
    mesh = build_slope(size=64, mirror=True, cols=8, rows=8)
    light, strength = (0.4, -0.6, 0.7), 0.9
    ref = _reference_irradiance(mesh, light, strength)

    summaries = []
    seam_max = []
    irr_max = []
    hi = None
    for seed in range(1, 6):
        sizes = {c["id"]: c["size"] for c in mesh["charts"]}
        _, placements = pack_random(sizes, seed)
        data = {"charts": mesh["charts"], "light": list(light),
                "strength": strength, "padding": 2, "levels": 4}
        res = bake(data, placements=placements)
        summaries.append(_summary(res))

        # High-resolution reference bake once, at 4x resolution.
        if hi is None:
            hi_mesh = build_slope(size=256, mirror=True, cols=8, rows=8)
            hi_data = {"charts": hi_mesh["charts"], "light": list(light),
                       "strength": strength, "padding": 2, "levels": 4}
            hi = bake(hi_data)

        # Irradiance vs reference over canonical texel centres.
        worst = 0.0
        for cid in sizes:
            entry = res["charts"][cid]["levels"][0]
            ref_entry = hi["charts"][cid]["levels"][0]
            w = entry["size"]
            rw = ref_entry["size"]
            for y in range(w):
                for x in range(w):
                    u = (x + 0.5) / w
                    v = (y + 0.5) / w
                    got = sample_chart(entry, u, v)
                    rv = sample_chart(ref_entry, u, v)
                    worst = max(worst, abs(got - rv))
        irr_max.append(worst)

        # Seam: paired samples on duplicated vertices, same 3D position.
        smax = 0.0
        lvl0 = res["atlas"][0]
        for pair in mesh["seam"]:
            lu, lv = pair["left_uv"]
            ru, rv = pair["right_uv"]
            lv_l = sample_chart(res["charts"]["L"]["levels"][0], lu, lv)
            lv_r = sample_chart(res["charts"]["R"]["levels"][0], ru, rv)
            smax = max(smax, abs(lv_l - lv_r))
            # Also exercise the atlas path with owner verification.
            at_l = sample_atlas(lvl0, placements["L"], "L", lu, lv,
                                sizes["L"])
            at_r = sample_atlas(lvl0, placements["R"], "R", ru, rv,
                                sizes["R"])
            check(at_l is not None and at_r is not None,
                  "A2: atlas seam taps must resolve to owned texels")
            check(abs(at_l - lv_l) <= 1e-12 and abs(at_r - lv_r) <= 1e-12,
                  "A2: atlas and canonical sampling disagree")
        seam_max.append(smax)

        # Inverse mapping: every atlas texel maps back to canonical space.
        lvl = res["atlas"][0]
        side = lvl["size"]
        mapped = 0
        for cid, p in placements.items():
            rect = sizes[cid]
            for ay in range(p["y"], p["y"] + rect):
                for ax in range(p["x"], p["x"] + rect):
                    loc = invert_atlas_texel(cid, ax, ay, p, rect)
                    check(loc is not None, "A2: inverse texel failed")
                    if loc:
                        mapped += 1
        check(mapped == sum(s * s for s in sizes.values()),
              "A2: inverse texel count mismatch")
    check(all(s == summaries[0] for s in summaries[1:]),
          "A2: canonical summaries differ across the five packings")
    check(max(irr_max) <= 2e-3,
          "A2: irradiance vs hi-res reference %.3e" % max(irr_max))
    check(max(seam_max) <= 1e-4,
          "A2: seam jump %.3e > 1e-4" % max(seam_max))
    check(abs(ref - sample_chart(hi["charts"]["L"]["levels"][0], 0.3, 0.4))
          <= 2e-3, "A2: analytic reference disagrees with hi-res bake")
    # Direct analytic check over every valid low-res texel of both charts.
    analytic_worst = 0.0
    for cid in sizes:
        for v in res["charts"][cid]["levels"][0]["light"]:
            if v is not None:
                analytic_worst = max(analytic_worst, abs(v - ref))
    check(analytic_worst <= 2e-3,
          "A2: analytic irradiance mismatch %.3e" % analytic_worst)
    notes.append("A2 slope: 5 packings identical summaries; irr err %.2e; "
                 "seam %.2e; analytic ref %.5f"
                 % (max(irr_max), max(seam_max), ref))


def test_order_independence():
    mesh = build_slope(size=32, mirror=True)
    data = {"charts": mesh["charts"], "light": [0.3, 0.5, 0.8],
            "strength": 0.7, "padding": 2, "levels": 3}
    base = bake(data)
    rng = LCG(12345)
    shuffled = json.loads(json.dumps(data))
    rng.shuffle(shuffled["charts"])
    for c in shuffled["charts"]:
        rng.shuffle(c["triangles"])
    other = bake(shuffled)
    check(_summary(base) == _summary(other),
          "A3: shuffled input changed canonical arrays")

    # Reordered atlas writes: assemble the same level twice visiting
    # chart ids in opposite orders and compare the final grids.  The
    # deterministic winner rule must make the result identical.
    from lightmap.atlas import blit_level
    sizes = {c["id"]: c["size"] for c in data["charts"]}
    chains = {}
    for cid, ch in base["charts"].items():
        chains[cid] = [
            {"size": e["size"], "light": e["light"], "owner": e["owners"]}
            for e in ch["levels"]
        ]
    pl = base["placements"]

    def assemble(reverse_ids):
        ids = sorted(pl, reverse=reverse_ids)
        grids = []
        for level in range(len(next(iter(chains.values())))):
            entry = blit_level(
                {cid: pl[cid] for cid in ids}, sizes, chains, level)
            grids.append((entry["light"], entry["owner"],
                          [(c["atlas"], c["winner"]) for c in entry["conflicts"]]))
        return grids

    forward = assemble(False)
    backward = assemble(True)
    check(forward == backward, "A3: atlas output depends on write order")
    notes.append("A3 order: canonical arrays and atlas stable under reorder")


def test_demo_and_rotation():
    demo = json.loads(Path("demo.json").read_text())
    res = bake(demo)
    check(set(res["charts"]) == {"bright", "dark", "slope"},
          "A4: demo chart ids")
    lvl0 = res["atlas"][0]
    check(lvl0["size"] >= 16, "A4: demo atlas side")
    # Rotation convention: 90 cw maps (x,y) -> (N-1-y, x); inverse round trip.
    n = 8
    for rot in (0, 90, 180, 270):
        for x in range(n):
            for y in range(n):
                rx, ry = rotate_pixel(x, y, n, rot)
                bx, by = inverse_pixel(rx, ry, n, rot)
                check((bx, by) == (x, y),
                      "A4: rotation round trip rot=%d" % rot)
    # Traceability of a covered bright texel.
    tr = res["trace"]["bright"]["pixels"]
    traced = [t for t in tr if t and t["mask"] == "cover"]
    check(traced and all(t["triangle"] is not None and len(t["normal"]) == 3
                        for t in traced), "A4: trace records incomplete")
    # No null light remains inside any chart chain after dilation.
    for cid, ch in res["charts"].items():
        for e in ch["levels"]:
            check(all(v is not None for v in e["light"]),
                  "A4: undilated null inside chart %s" % cid)
    notes.append("A4 demo/rotation: %d traced bright texels; round trips ok"
                 % len(traced))


def main():
    test_bright_dark()
    test_partial_bright_dilation()
    test_slope_packings()
    test_order_independence()
    test_demo_and_rotation()
    print("=" * 68)
    for line in notes:
        print(" -", line)
    print("=" * 68)
    if failures:
        print("FAILED %d check(s):" % len(failures))
        for f in failures:
            print("  *", f)
        raise SystemExit(1)
    print("ALL ACCEPTANCE CHECKS PASSED")


if __name__ == "__main__":
    main()
