"""Acceptance checks for terrain.py output."""
import json
import sys

import terrain

TOL = 1e-5


def tri_area_xy(a, b, c):
    return 0.5 * ((b[0] - a[0]) * (c[1] - a[1]) - (c[0] - a[0]) * (b[1] - a[1]))


def check_frames(out, label):
    for fi, frame in enumerate(out["frames"]):
        at = {(t["bounds"][0], t["bounds"][1]): t for t in frame["tiles"]}
        # per-tile single-layer coverage of the whole unit square
        for t in frame["tiles"]:
            v = t["vertices"]
            area = 0.0
            x0, y0, x1, y1 = t["bounds"]
            for f in t["faces"]:
                a, b, c = v[f[0]], v[f[1]], v[f[2]]
                ar = tri_area_xy(a, b, c)
                assert ar > 0, (label, fi, t["id"], "degenerate or CW triangle")
                area += ar
                for p in (a, b, c):
                    assert x0 - TOL <= p[0] <= x1 + TOL, (label, fi, t["id"], "x out")
                    assert y0 - TOL <= p[1] <= y1 + TOL, (label, fi, t["id"], "y out")
                    # z must be the analytic height: single coverage source
                    assert abs(p[2] - terrain.height(p[0], p[1])) <= TOL, \
                        (label, fi, t["id"], "z mismatch")
            assert abs(area - 1.0) <= TOL, (label, fi, t["id"], "area", area)
            assert t["triangles"] == len(t["faces"])
        # shared edges: identical world-point sequences on both sides,
        # so no crack can open along the common border
        for t in frame["tiles"]:
            x0, y0 = t["bounds"][0], t["bounds"][1]
            for side, oside, dx, dy in (("east", "west", 1, 0),
                                        ("north", "south", 0, 1)):
                nb = at.get((x0 + dx, y0 + dy))
                if nb is None:
                    continue
                e1, e2 = t["edges"][side], nb["edges"][oside]
                assert len(e1) == len(e2), (label, fi, t["id"], side,
                                            "edge point count", len(e1), len(e2))
                for p, q in zip(e1, e2):
                    d = max(abs(p[0] - q[0]), abs(p[1] - q[1]), abs(p[2] - q[2]))
                    assert d <= TOL, (label, fi, t["id"], side, "seam gap", d)
        # four-tile corner: every incident tile samples the same corner point
        corners = {}
        for t in frame["tiles"]:
            x0, y0, x1, y1 = t["bounds"]
            for cx, cy in ((x0, y0), (x1, y0), (x0, y1), (x1, y1)):
                hits = [v for e in t["edges"].values() for v in e
                        if abs(v[0] - cx) <= TOL and abs(v[1] - cy) <= TOL]
                corners.setdefault((cx, cy), []).extend(hits)
        for c, hits in corners.items():
            zs = [h[2] for h in hits]
            assert zs and max(zs) - min(zs) <= TOL, (label, fi, "corner", c)
    print(label, "seam/coverage checks OK")


def check_hysteresis():
    # camera oscillating across the 1.2 up-threshold: error 1.3 <-> 1.1
    errs = [1.3] + [1.1, 1.3] * 4
    frames = [{"tiles": [{"id": 0, "x": 0, "y": 0, "error": e, "ready": True}]}
              for e in errs]
    out = terrain.replay({"frames": frames})
    levels = [f["levels"][0] for f in out["frames"]]
    switches = sum(f["switches"] for f in out["frames"])
    assert levels == [1] * len(errs), levels       # 1.1 >= 0.8: hold band
    assert switches == 1, switches                 # one switch, no flapping
    # deep oscillation 1.3 <-> 0.5 crosses both bands: switches every frame
    errs = [1.3, 0.5] * 4
    frames = [{"tiles": [{"id": 0, "x": 0, "y": 0, "error": e, "ready": True}]}
              for e in errs]
    out = terrain.replay({"frames": frames})
    levels = [f["levels"][0] for f in out["frames"]]
    assert levels == [1, 0] * 4, levels
    print("hysteresis checks OK: hold band honoured, no flapping")


def check_order_independence(scene):
    base = terrain.replay(scene)
    rev = {"frames": [{"tiles": list(reversed(f["tiles"]))}
                      for f in scene["frames"]]}
    flipped = terrain.replay(rev)
    assert json.dumps(base, sort_keys=True) == json.dumps(flipped, sort_keys=True), \
        "reversed load order changed the result"
    print("order-independence OK: reversed load order, identical depth summary")


def check_geometry_budget(out, n_tiles):
    uniform_finest = 2 * terrain.SEGMENTS[-1] ** 2 * n_tiles
    for fi, f in enumerate(out["frames"]):
        assert f["triangles"] < uniform_finest, (fi, f["triangles"])
    print("geometry budget OK: every frame below uniform finest grid"
          " (< %d triangles)" % uniform_finest)


def stress_scene():
    # 5x5 grid: level-2 centre, level-1 ring, level-0 outside.  Exercises
    # n=8 tiles collapsing all four sides at once (corner blocks + strips)
    # and level differences on every edge orientation.
    tiles = []
    tid = 0
    for y in range(5):
        for x in range(5):
            d = max(abs(x - 2), abs(y - 2))
            err = (3.0, 1.5, 0.1)[d]
            tiles.append({"id": tid, "x": x, "y": y, "error": err,
                          "ready": True})
            tid += 1
    # hold the same camera for three frames, then pull far away
    frames = [{"tiles": tiles}] * 3
    far = [dict(t, error=0.1) for t in tiles]
    frames += [{"tiles": far}] * 2
    return {"frames": frames}


def main():
    scene = json.loads(open("demo.json").read())
    out = terrain.replay(scene)
    check_frames(out, "demo")
    check_hysteresis()
    check_order_independence(scene)
    check_geometry_budget(out, len(scene["frames"][0]["tiles"]))
    stress = stress_scene()
    sout = terrain.replay(stress)
    assert sout["frames"][0]["levels"][12] == 2  # centre keeps finest level
    check_frames(sout, "stress")
    check_order_independence(stress)
    check_geometry_budget(sout, 25)
    print("ALL CHECKS PASSED")


if __name__ == "__main__":
    sys.exit(main())
