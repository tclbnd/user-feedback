"""Extra stress checks: budget=1 universal fallback, demo parity, validation."""
import json

import composite
import test_oit

# 1) demo.json parity with the reference
scene = json.load(open("demo.json"))
a = composite.render(scene, 4, 16)["rgba"]
b = composite.render_reference(scene)["rgba"]
err = max(abs(a[y][x][c] - b[y][x][c])
          for y in range(1) for x in range(1) for c in range(4))
print("demo max err:", err)
assert err <= 1e-15

# 2) budget=1: every non-empty ray takes the accurate fallback
big, _meta = test_oit.build_scene()
r1 = composite.render(big, 3, 1)
ref = composite.render_reference(big)["rgba"]
err = max(abs(r1["rgba"][y][x][c] - ref[y][x][c])
          for y in range(test_oit.H)
          for x in range(test_oit.W) for c in range(4))
st = r1["stats"]
print("budget=1 err:", err, "peak:", st["peak_fragment_records"],
      "fallback invocations:", st["fallback_invocations"],
      "overflow pixels:", st["overflow_pixels"],
      "region:", st["fallback_region"])
assert err <= test_oit.TOL
assert st["peak_fragment_records"] <= 1
fb = {tuple(p) for p in st["fallback_pixels"]}
# With budget=1 a ray falls back iff it holds >= 2 valid fragments; rays with
# exactly one valid record still use the bounded cached path.
counts = {}
for f in big["fragments"]:
    if composite.valid_fragment(f,
                                big["opaque"][f["y"]][f["x"]][f["sample"]]):
        key = (f["x"], f["y"], f["sample"])
        counts[key] = counts.get(key, 0) + 1
nonempty = {(x, y) for (x, y, _s), n in counts.items() if n > 1}
assert fb == nonempty, (len(fb), len(nonempty))
print("all", len(fb), "over-capacity pixels correctly used the fallback")

# 3) argument validation
for kw in (dict(tile=0, budget=4), dict(tile=4, budget=0),
           dict(tile=-1, budget=4)):
    try:
        composite.render(big, **kw)
        raise AssertionError("should have raised")
    except ValueError:
        pass
print("argument validation OK")

# 4) gamma path preserves alpha and only transforms resolved RGB
lin = composite.render(big, 2, 6)
gam = composite.render(big, 2, 6, gamma=2.2)
for y in range(test_oit.H):
    for x in range(test_oit.W):
        assert abs(gam["rgba"][y][x][3] - lin["rgba"][y][x][3]) < 1e-15
        for c in range(3):
            assert abs(gam["rgba"][y][x][c]
                       - lin["rgba"][y][x][c] ** (1 / 2.2)) < 1e-15
print("gamma path OK")

# 5) zero-alpha rays never trigger fallback even with tiny budget
zscene = {
    "size": [1, 1], "samples": 1,
    "opaque": [[[{"z": None, "rgba": [0, 0, 0, 0]}]]],
    "fragments": [
        {"id": i, "x": 0, "y": 0, "sample": 0, "z": 0.1 * i,
         "rgba": [0, 0, 0, 0]} for i in range(1, 50)
    ],
}
rz = composite.render(zscene, 1, 2)
assert rz["stats"]["overflow_pixels"] == 0
assert rz["stats"]["fallback_invocations"] == 0
assert rz["rgba"] == [[[0.0, 0.0, 0.0, 0.0]]]
print("zero-alpha-only ray: no fallback, transparent output OK")

print("STRESS CHECKS PASSED")
