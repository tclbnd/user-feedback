#!/usr/bin/env python3
"""Structural validation of output.json against the README contract."""
import json

o = json.load(open("output.json"))
d = json.load(open("demo.json"))

assert [im["id"] for im in o["images"]] == [r["id"] for r in d["renders"]]
for im, r in zip(o["images"], d["renders"]):
    cov = im["coverage"]
    assert len(cov) == r["height"]
    assert all(len(row) == r["width"] for row in cov)
    flat = [v for row in cov for v in row]
    assert all(0.0 <= v <= 1.0 for v in flat)

at = o["atlas"]
assert len(at["data"]) == at["width"] * at["height"]
for gid, g in o["glyphs"].items():
    x, y, w, h = g["atlas_rect"]
    assert 0 <= x and 0 <= y and x + w <= at["width"] and y + h <= at["height"]

vals = [v for v in at["data"] if abs(v) < 100]
neg = sum(1 for v in vals if v < 0)
print("keys:", sorted(o.keys()))
print("glyphs:", {k: v["atlas_rect"] for k, v in o["glyphs"].items()})
print("atlas: %dx%d tpe=%d padding=%d" % (at["width"], at["height"],
                                          at["texels_per_em"], at["padding"]))
print("sdf range: [%.4f, %.4f] em, %d negative texels" % (min(vals), max(vals), neg))
print("images:", len(o["images"]), "structure OK")

