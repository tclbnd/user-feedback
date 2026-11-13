import math

from geometry import (equilateral_prism, polygon_normals,
                      ray_polygon_intersect, refract_vector, trace_polygon,
                      unit_from_angle, angle_of)

# Unit square centred on origin, CCW
sq = [(-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)]
nrms = polygon_normals(sq)
print("normals", nrms)
# right edge normal points +x, top +y, etc.
assert abs(nrms[1][0] - 1.0) < 1e-12
assert abs(nrms[2][1] - 1.0) < 1e-12

hit = ray_polygon_intersect((-2.0, 0.0), unit_from_angle(0.0), sq, nrms)
assert hit is not None
t, edge, p, n = hit
print("square hit", t, edge, p, n)
# Traveling +x from x=-2 the first face met is the left edge x=-1.
assert edge == 3 and abs(p[0] + 1.0) < 1e-9 and abs(p[1]) < 1e-9
assert abs(n[0] + 1.0) < 1e-12

# The far edge is found after entering (skip the entry edge, ray inside).
hit2 = ray_polygon_intersect((0.0, 0.0), unit_from_angle(0.0), sq, nrms,
                             skip=3, inside=True)
t2, edge2, p2, n2 = hit2
assert edge2 == 1 and abs(p2[0] - 1.0) < 1e-9 and abs(n2[0] - 1.0) < 1e-12

# Ray missing the polygon
assert ray_polygon_intersect((-2.0, 2.0), unit_from_angle(0.0), sq,
                             nrms) is None

# Vector Snell against the medium module's scalar form at 30 deg incidence
d = unit_from_angle(30.0)
n = (-1.0, 0.0)  # outward normal of entry face, pointing toward ray
td, tir = refract_vector(d, n, 1.0, 1.5)
print("vector refract", angle_of(td), "tir", tir)
expected = math.degrees(math.asin(math.sin(math.radians(30)) / 1.5))
assert abs(angle_of(td) - expected) < 1e-12 and not tir

# Prism: constant IOR must give identical exit angles for all wavelengths
tri = equilateral_prism((0.0, 0.0), 2.0, rotation_deg=-40.0)
res = trace_polygon((-3.0, -0.3), 0.0, tri, [1.5, 1.5, 1.5])
for r in res:
    print("flat prism", r["exit_angle"], r["exit_point"], r["path_length"],
          r["ok"], r["tir_bounces"])
    assert r["ok"] and not r["tir_bounces"]
assert max(abs(r["exit_angle"] - res[0]["exit_angle"]) for r in res) < 1e-12
assert max(math.dist(r["exit_point"], res[0]["exit_point"])
           for r in res) < 1e-9

# Dispersive prism: longer wavelengths (lower IOR here) bend less
ior = [1.55, 1.54, 1.53, 1.52, 1.51]
res2 = trace_polygon((-3.0, -0.3), 0.0, tri, ior)
ys = [r["exit_point"][1] for r in res2]
angs = [r["exit_angle"] for r in res2]
print("dispersion exit y:", ys)
print("dispersion exit angles:", angs)
for r in res2:
    assert r["ok"] and not r["tir_bounces"]
    assert r["path_length"] > 0.0
# As IOR decreases with wavelength index, bending magnitude must shrink
# monotonically and the exit positions must separate cleanly.
diffs_y = [ys[i + 1] - ys[i] for i in range(len(ys) - 1)]
diffs_a = [angs[i + 1] - angs[i] for i in range(len(angs) - 1)]
print("dy", diffs_y, "da", diffs_a)
assert all(v < -1e-6 for v in diffs_y)
assert all(v < -1e-6 for v in diffs_a)
spread = max(ys) - min(ys)
print("ribbon spread", spread)
assert spread > 0.01
print("geometry tests OK")
