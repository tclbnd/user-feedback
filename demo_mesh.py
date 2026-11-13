"""Inspect shared edges, mirrored UV winding and normal direction."""
from lightmap.mesh import build_slope
from lightmap.raster import raster_chart

mesh = build_slope(size=16, mirror=True, cols=8, rows=8)
print("edge report:", mesh["edge_report"])
print("seam pairs:", len(mesh["seam"]), "first:", mesh["seam"][0])

left, right = mesh["charts"]
lc = raster_chart(left)
rc = raster_chart(right)
l_mirror = sorted({c["mirrored"] for c in lc if c})
r_mirror = sorted({c["mirrored"] for c in rc if c})
print("left mirrored flags:", l_mirror, "right mirrored flags:", r_mirror)
print("left covered:", sum(c is not None for c in lc),
      "right covered:", sum(c is not None for c in rc))

# A mirrored chart must keep geometric normal direction (no flip).
n_left = [c["normal"] for c in lc if c][0]
n_right = [c["normal"] for c in rc if c][0]
print("left normal:", [round(v, 5) for v in n_left])
print("right normal:", [round(v, 5) for v in n_right])
assert n_left[2] > 0 and n_right[2] > 0, "mirrored UV flipped the normal"
assert r_mirror == [True] and l_mirror == [False]

# Duplicated seam vertices: the same 3D position exists once per chart
# as an independent UV vertex (mirror case shares the u=1 edge; the
# non-mirrored case splits the edge across u=1 and u=0).
s0 = mesh["seam"][0]
plain = build_slope(size=16, mirror=False, cols=8, rows=8)
p0 = plain["seam"][0]
assert p0["left_uv"] != p0["right_uv"], "non-mirror seam should split UVs"
assert s0["left_uv"] == [1.0, s0["left_uv"][1]]
# And the two charts really carry distinct vertex records.
assert mesh["charts"][0] is not mesh["charts"][1]
print("OK: mirrored UV handled; normals stay geometric; seam duplicated")
