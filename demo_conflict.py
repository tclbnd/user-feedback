"""Force a coarse-mip collision and verify the no-mix conflict policy."""
from lightmap import bake
from run_tests import full_cover_chart

# Two 4px charts whose origins differ by 2px.  At level 2 each rect
# shrinks to one texel and origin/4 maps both onto atlas cell 0, so a
# real collision is recorded instead of being blended.
charts = [
    full_cover_chart("aaa", 4, ambient=1.0, normal=[0, 0, 1]),
    full_cover_chart("zzz", 4, ambient=0.0, normal=[0, 0, -1]),
]
charts[0]["origin"] = [0, 0]
charts[1]["origin"] = [2, 0]
data = {"charts": charts, "light": [0, 0, 1], "strength": 0.0,
        "padding": 0, "levels": 3}
res = bake(data)
for lvl in res["atlas"]:
    print("level", lvl["level"], "side", lvl["size"],
          "conflicts", len(lvl["conflicts"]))
    for c in lvl["conflicts"]:
        print("  ", c)
# Canonical chains must remain pristine regardless of atlas collisions.
for cid in ("aaa", "zzz"):
    for e in res["charts"][cid]["levels"]:
        vals = [v for v in e["light"] if v is not None]
        assert vals and min(vals) == max(vals)
    print(cid, "canonical chain constant =",
          res["charts"][cid]["levels"][-1]["light"][0])
# The collision cell keeps one claimant's exact value, never an average.
last = res["atlas"][-1]
owned = [(k, o, last["light"][k]) for k, o in enumerate(last["owners"])
         if o is not None]
print("coarse owned cells:", owned)
assert all(v in (0.0, 1.0) for _, _, v in owned), "mixed value detected"
print("OK: no cross-chart averaging under atlas collision")
