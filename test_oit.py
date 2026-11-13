"""Acceptance tests for the bounded-memory OIT compositor.

Coverage:
  * overlapping thin sheets (many fragments, close depths),
  * equal-depth fragments with a stable intrinsic sequence number (id),
  * deep rays at ~8x the memory budget (exact fallback, no dropped records),
  * a transparent object crossing an opaque surface (per-sample depth test),
  * alpha == 0 / alpha == 1 / extremely small alpha edge cases,
  * two submission orders x two tile sizes, with RGBA and overflow stats
    compared, plus an explicit residency-vs-budget simulation.
"""

import copy
import json
import subprocess
import sys
from pathlib import Path

import composite

TOL = 2e-6
W, H, SAMPLES = 10, 6, 4
BUDGET = 6
TILES = (2, 5)


# ---------------------------------------------------------------------------
# Deterministic RNG (no third-party dependency)
# ---------------------------------------------------------------------------

class Lcg:
    def __init__(self, seed=0x1234ABCD):
        self.state = seed & 0xFFFFFFFF

    def next_u32(self):
        self.state = (self.state * 1664525 + 1013904223) & 0xFFFFFFFF
        return self.state

    def unit(self):
        return self.next_u32() / 4294967296.0

    def between(self, lo, hi):
        return lo + (hi - lo) * self.unit()


def build_scene():
    rng = Lcg()
    # Opaque background: some samples have no surface (z=None), others sit at
    # varied depths so transparent fragments can cross the surface per sample.
    opaque = []
    for y in range(H):
        row = []
        for x in range(W):
            samples = []
            for s in range(SAMPLES):
                if (x + y + s) % 7 == 0:
                    z = None
                else:
                    z = round(rng.between(0.45, 0.95), 6)
                base = [round(rng.between(0.0, 0.25), 6) for _ in range(3)]
                base.append(1.0 if z is not None else 0.0)
                samples.append({"z": z, "rgba": base})
            row.append(samples)
        opaque.append(row)

    frags = []
    next_id = 1

    def add(x, y, s, z, rgba):
        nonlocal next_id
        frags.append({"id": next_id, "x": x, "y": y, "sample": s,
                      "z": round(z, 6),
                      "rgba": [round(v, 6) for v in rgba]})
        next_id += 1

    # 1) General coverage: random thin sheets, including alpha edge cases.
    for y in range(H):
        for x in range(W):
            for s in range(SAMPLES):
                n = 1 + rng.next_u32() % 5
                for k in range(n):
                    z = rng.between(0.05, 0.98)
                    pick = rng.next_u32() % 10
                    if pick == 0:
                        a = 0.0                       # zero alpha
                    elif pick == 1:
                        a = 1.0                       # fully opaque glass
                    elif pick == 2:
                        a = 1e-7                      # extremely low alpha
                    else:
                        a = rng.between(0.02, 0.6)
                    rgb = [rng.between(0.0, 0.5) for _ in range(3)]
                    # premultiplied: rgb must not exceed coverage
                    rgb = [c * a for c in rgb]
                    add(x, y, s, z, rgb + [a])

    # 2) Equal-depth fragments with a stable sequence: same (x, y, s, z),
    #    different ids; their order must follow id regardless of submission.
    tie_x, tie_y, tie_s = 3, 2, 1
    tie_z = 0.333333
    for k in range(5):
        a = 0.25
        add(tie_x, tie_y, tie_s, tie_z,
            [0.1 * (k + 1) * a, 0.0, 0.0, a])

    # 3) Deep ray at ~8x budget: 48 valid fragments on one sample
    #    (budget is 6 -> 8x).  Includes an opaque fragment in the middle.
    deep_x, deep_y, deep_s = 7, 4, 2
    opaque[deep_y][deep_x][deep_s] = {"z": 0.99, "rgba": [0.02, 0.05, 0.02, 1]}
    for k in range(48):
        z = 0.1 + 0.01 * k + rng.between(0.0, 0.002)
        if k == 20:
            rgba = [0.3, 0.2, 0.1, 1.0]              # alpha == 1
        else:
            a = rng.between(0.03, 0.4)
            rgba = [rng.between(0, 0.4) * a,
                    rng.between(0, 0.4) * a,
                    rng.between(0, 0.4) * a, a]
        add(deep_x, deep_y, deep_s, z, rgba)

    # 4) Transparent object crossing an opaque surface: same fragment depth z
    #    on all four samples, but the opaque surface depth varies per sample,
    #    so the fragment is in front on some samples and occluded on others.
    cross_x, cross_y = 5, 1
    cross_z = 0.6
    for s in range(SAMPLES):
        op = opaque[cross_y][cross_x][s]
        op["z"] = 0.4 if s % 2 == 0 else 0.8
        op["rgba"] = [0.05, 0.05, 0.05, 1.0]
        add(cross_x, cross_y, s, cross_z,
            [0.4 * 0.5, 0.1 * 0.5, 0.2 * 0.5, 0.5])

    scene = {"size": [W, H], "samples": SAMPLES,
             "opaque": opaque, "fragments": frags}
    meta = {"tie": (tie_x, tie_y, tie_s),
            "deep": (deep_x, deep_y, deep_s),
            "cross": (cross_x, cross_y)}
    return scene, meta


def block_reversed_order(fragments, block):
    """A submission order different from source order but id-stable."""
    order = list(range(len(fragments)))
    out = []
    for i in range(0, len(order), block):
        out.extend(reversed(order[i:i + block]))
    return out


def reorder(scene, perm):
    s2 = copy.deepcopy(scene)
    s2["fragments"] = [scene["fragments"][i] for i in perm]
    return s2


def max_channel_error(a, b):
    worst = 0.0
    where = None
    for y in range(len(a)):
        for x in range(len(a[0])):
            for c in range(4):
                d = abs(a[y][x][c] - b[y][x][c])
                if d > worst:
                    worst = d
                    where = (x, y, c)
    return worst, where


# ---------------------------------------------------------------------------
# Explicit residency simulation mirroring render()'s caching discipline.
# Proves simultaneously resident transparent records never exceed budget.
# ---------------------------------------------------------------------------

def simulate_residency(scene, tile, budget):
    width, height = scene["size"]
    opaque = scene["opaque"]
    fragments = scene["fragments"]
    peak = 0
    for ty0 in range(0, height, tile):
        for tx0 in range(0, width, tile):
            groups = {}
            for idx, f in enumerate(fragments):
                x, y, s = f["x"], f["y"], f["sample"]
                if not (tx0 <= x < tx0 + tile and ty0 <= y < ty0 + tile):
                    continue
                if not composite.valid_fragment(f, opaque[y][x][s]):
                    continue
                groups.setdefault((x, y, s), []).append(idx)
            cache = []
            for key, ids in groups.items():
                if len(ids) > budget:
                    # fallback rank scans hold exactly one record
                    peak = max(peak, 1)
                    continue
                if len(cache) + len(ids) > budget:
                    peak = max(peak, len(cache))
                    cache = []
                cache.extend(ids)
                peak = max(peak, len(cache))
            peak = max(peak, len(cache))
    return peak


def main():
    scene, meta = build_scene()
    perms = {
        "source_order": list(range(len(scene["fragments"]))),
        "block_reversed": block_reversed_order(scene["fragments"], 37),
    }

    print("== independent reference (per-ray sort, back-to-front OVER) ==")
    ref = composite.render_reference(scene)["rgba"]

    results = {}
    all_ok = True
    for order_name, perm in perms.items():
        for tile in TILES:
            tag = f"{order_name}, tile={tile}"
            sc = reorder(scene, perm)
            res = composite.render(sc, tile, BUDGET)
            results[tag] = res
            err, where = max_channel_error(res["rgba"], ref)
            ok = err <= TOL
            all_ok &= ok
            st = res["stats"]
            print(f"[{tag}] max RGBA error={err:.3e} at (x,y,c)={where} "
                  f"<= {TOL:.0e}: {'OK' if ok else 'FAIL'}")
            print(f"    overflow_pixels={st['overflow_pixels']} "
                  f"fallback_invocations={st['fallback_invocations']} "
                  f"peak_records={st['peak_fragment_records']} "
                  f"budget={BUDGET} "
                  f"fallback_region={st['fallback_region']}")

    print("\n== submission-order / tile-size invariance ==")
    base_tag = "source_order, tile=2"
    base = results[base_tag]
    for tag, res in results.items():
        err, where = max_channel_error(res["rgba"], base["rgba"])
        same_rgba = err == 0.0
        same_stats = (res["stats"]["overflow_pixels"]
                      == base["stats"]["overflow_pixels"]
                      and res["stats"]["fallback_invocations"]
                      == base["stats"]["fallback_invocations"]
                      and res["stats"]["valid_fragments"]
                      == base["stats"]["valid_fragments"]
                      and res["stats"]["zero_alpha_fragments"]
                      == base["stats"]["zero_alpha_fragments"]
                      and res["stats"]["occluded_fragments"]
                      == base["stats"]["occluded_fragments"])
        all_ok &= same_rgba and same_stats
        print(f"[{tag}] bit-identical RGBA vs [{base_tag}]: {same_rgba}; "
              f"overflow stats identical: {same_stats}")

    print("\n== memory budget enforcement ==")
    for order_name, perm in perms.items():
        for tile in TILES:
            sc = reorder(scene, perm)
            peak = simulate_residency(sc, tile, BUDGET)
            actual = results[f"{order_name}, tile={tile}"][
                "stats"]["peak_fragment_records"]
            ok = peak <= BUDGET and actual <= BUDGET and peak == actual
            all_ok &= ok
            print(f"[{order_name}, tile={tile}] simulated peak={peak} "
                  f"reported peak={actual} budget={BUDGET}: "
                  f"{'OK' if ok else 'FAIL'}")

    print("\n== deep-ray fallback accuracy (8x budget, no dropped records) ==")
    dx, dy, ds = meta["deep"]
    deep_res = results[base_tag]
    deep_ref_px = ref[dy][dx]
    deep_err = max(abs(deep_res["rgba"][dy][dx][c] - deep_ref_px[c])
                   for c in range(4))
    fallback_pixels = {tuple(p) for p in
                       deep_res["stats"]["fallback_pixels"]}
    region = deep_res["stats"]["fallback_region"]
    ok = (deep_err <= TOL and (dx, dy) in fallback_pixels
          and region is not None
          and region["x0"] <= dx <= region["x1"]
          and region["y0"] <= dy <= region["y1"])
    all_ok &= ok
    print(f"deep pixel error={deep_err:.3e}; pixel reported as fallback: "
          f"{(dx, dy) in fallback_pixels}; region={region}: "
          f"{'OK' if ok else 'FAIL'}")
    print(f"fallback pixel count (report): "
          f"{deep_res['stats']['overflow_pixels']}")

    print("\n== equal-depth stable ordering ==")
    tx, ty, ts = meta["tie"]
    for order_name in perms:
        r = results[f"{order_name}, tile=2"]
        e = max_channel_error(r["rgba"], ref)[0]
        all_ok &= e <= TOL
    # Direct unit check: reverse the five tie fragments' submission; the
    # intrinsic id sort must still produce the reference value.
    tie_scene = copy.deepcopy(scene)
    tie_frags = [f for f in tie_scene["fragments"]
                 if f["x"] == tx and f["y"] == ty and f["sample"] == ts
                 and abs(f["z"] - 0.333333) < 1e-9]
    assert len(tie_frags) == 5
    others = [f for f in tie_scene["fragments"] if f not in tie_frags]
    tie_scene["fragments"] = others + list(reversed(tie_frags))
    r_tie = composite.render(tie_scene, 2, BUDGET)["rgba"][ty][tx]
    e_tie = max(abs(r_tie[c] - ref[ty][tx][c]) for c in range(4))
    all_ok &= e_tie <= TOL
    print(f"tie-ray error after reversing submission: {e_tie:.3e}: "
          f"{'OK' if e_tie <= TOL else 'FAIL'}")

    print("\n== alpha edge cases ==")
    op = [{"z": 0.9, "rgba": [0.1, 0.2, 0.3, 1.0]}]
    # alpha 0, tiny alpha, alpha 1 behind/in front
    edge = {"size": [1, 1], "samples": 1, "opaque": [[op]],
            "fragments": [
                {"id": 1, "x": 0, "y": 0, "sample": 0, "z": 0.8,
                 "rgba": [0.0, 0.0, 0.0, 0.0]},          # zero alpha
                {"id": 2, "x": 0, "y": 0, "sample": 0, "z": 0.5,
                 "rgba": [1e-7, 2e-7, 3e-7, 1e-7]},      # tiny, kept
                {"id": 3, "x": 0, "y": 0, "sample": 0, "z": 0.4,
                 "rgba": [0.7, 0.1, 0.2, 1.0]},          # opaque, occludes
                {"id": 4, "x": 0, "y": 0, "sample": 0, "z": 0.2,
                 "rgba": [0.9, 0.9, 0.9, 0.0]},          # zero alpha
                {"id": 5, "x": 0, "y": 0, "sample": 0, "z": 0.95,
                 "rgba": [0.5, 0.5, 0.5, 0.5]},          # behind opaque z
            ]}
    out_edge = composite.render(edge, 1, 2)
    ref_edge = composite.render_reference(edge)["rgba"]
    e_edge = max(abs(out_edge["rgba"][0][0][c] - ref_edge[0][0][c])
                 for c in range(4))
    st_edge = out_edge["stats"]
    # id=5 is occluded by opaque z=0.9; two zero-alpha records are dropped.
    expected = [0.7, 0.1, 0.2, 1.0]
    got = out_edge["rgba"][0][0]
    ok = (e_edge <= TOL and st_edge["zero_alpha_fragments"] == 2
          and st_edge["occluded_fragments"] == 1
          and all(abs(got[c] - expected[c]) <= TOL for c in range(4)))
    all_ok &= ok
    print(f"edge RGBA={got} expected={expected} error={e_edge:.3e} "
          f"zero={st_edge['zero_alpha_fragments']} "
          f"occluded={st_edge['occluded_fragments']}: "
          f"{'OK' if ok else 'FAIL'}")

    print("\n== MSAA resolve separated from display encoding ==")
    lin = composite.render(scene, 2, BUDGET)["rgba"]
    gam = composite.render(scene, 2, BUDGET, gamma=2.2)["rgba"]
    # alpha channel untouched; RGB follows gamma on the resolved values
    for y in range(H):
        for x in range(W):
            assert abs(gam[y][x][3] - lin[y][x][3]) <= TOL
            for c in range(3):
                assert abs(gam[y][x][c] - lin[y][x][c] ** (1 / 2.2)) <= TOL
    print("gamma applied only after resolve, alpha preserved: OK")

    print("\n== CLI smoke test ==")
    tmp = Path("scene_tmp.json")
    tmp.write_text(json.dumps(scene))
    subprocess.run([sys.executable, "composite.py", str(tmp),
                    "out_tmp.json", "--tile", "4", "--budget", "16"],
                   check=True)
    cli = json.loads(Path("out_tmp.json").read_text())
    e_cli, _ = max_channel_error(cli["rgba"], ref)
    all_ok &= e_cli <= TOL
    tmp.unlink()
    Path("out_tmp.json").unlink()
    print(f"CLI output vs reference error={e_cli:.3e}: "
          f"{'OK' if e_cli <= TOL else 'FAIL'}")

    print("\n" + ("ALL ACCEPTANCE CHECKS PASSED" if all_ok
                  else "SOME CHECKS FAILED"))
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()