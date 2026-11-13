import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tessellate as T
import verify as V


def diagnose(name, err):
    with open(os.path.join(HERE, "scenes", name + ".json")) as f:
        sc = json.load(f)
    sc["err"] = err
    out = T.tessellate(sc)
    ec, es = V.mesh_edge_map(out["verts"], out["tris"])
    sing = [e for e, c in ec.items() if c == 1]
    rims = V.hole_rim_edges(sc["patches"], out["verts"], out["tris"])
    poles = V.pole_degenerate_segments(sc["patches"], out["verts"], out["tris"])
    bnd = V.boundary_segments(
        sc["patches"], dict((s["id"], s) for s in out["patch_stats"]))
    print("==", name, "err", err, "single edges:", len(sing),
          "rims:", len(rims), "poles:", len(poles), "boundary:", len(bnd))
    unexpl = [e for e in sing if es[e] not in rims and es[e] not in poles
              and es[e] not in bnd]
    print("unexplained:", len(unexpl))
    for e in unexpl[:12]:
        a, b = e
        print("  ", [round(x, 4) for x in out["verts"][a]],
              [round(x, 4) for x in out["verts"][b]])


if __name__ == "__main__":
    diagnose("saddle_hole", 0.05)
    diagnose("mixed_sphere", 0.05)
