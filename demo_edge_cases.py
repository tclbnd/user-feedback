"""Edge cases: degenerate UV triangles and the level-0 placement contract."""
from lightmap import bake
from lightmap.raster import raster_chart
from lightmap.atlas import rotate_pixel

# 1. Degenerate UV triangle contributes nothing, others still rasterise.
chart = {
    "id": "x", "size": 8,
    "triangles": [
        {"uv": [[0, 0], [0, 0], [1, 1]], "normal": [[0, 0, 1]] * 3},
        {"uv": [[0, 0], [1, 0], [1, 1]], "normal": [[0, 0, 1]] * 3},
        {"uv": [[0, 1], [1, 1], [0, 0]], "normal": [[0, 0, 1]] * 3},
    ],
}
cov = raster_chart(chart)
assert sum(c is not None for c in cov) == 64, "degenerate tri broke coverage"
print("degenerate UV ignored; covered =", sum(c is not None for c in cov))

# 2. README rotation convention on an explicit origin/rotate chart.
data = {"charts": [
    {"id": "c", "size": 4, "origin": [2, 3], "rotate": 90, "ambient": 0.5,
     "triangles": [
         {"uv": [[0, 0], [1, 0], [1, 1]], "normal": [[0, 0, 1]] * 3},
         {"uv": [[0, 1], [1, 1], [0, 0]], "normal": [[0, 0, 1]] * 3},
     ]}],
    "light": [0, 0, 1], "strength": 0.0, "padding": 0, "levels": 1}
res = bake(data)
p = res["placements"]["c"]
assert (p["x"], p["y"], p["rotate"]) == (2, 3, 90)
lvl = res["atlas"][0]
# Canonical local (0,0) rotates to (3,0), then offset by origin (2,3).
rx, ry = rotate_pixel(0, 0, 4, 90)
idx = (3 + ry) * lvl["size"] + (2 + rx)
assert lvl["owners"][idx] == "c" and abs(lvl["light"][idx] - 0.5) < 1e-12
print("level-0 origin/rotate honoured at atlas", (2 + rx, 3 + ry))

# 3. levels beyond representable size is clamped, never crashes.
data["levels"] = 9
res2 = bake(data)
assert len(res2["charts"]["c"]["levels"]) == 3  # 4 -> 2 -> 1
print("level clamp:", [e["size"] for e in res2["charts"]["c"]["levels"]])
print("OK: degenerate UV, placement contract and level clamping")
