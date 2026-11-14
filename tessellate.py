#!/usr/bin/env python3
"""Adaptive parametric tessellator (cracked baseline)."""
import json, sys, math, hashlib

def sphere_pt(uv, u, v, c, r):
    u0,v0,u1,v1 = uv
    lon = (u0 + u*(u1-u0)) * 2*math.pi
    lat = (v0 + v*(v1-v0) - 0.5) * math.pi
    return [c[0]+r*math.cos(lat)*math.cos(lon),
            c[1]+r*math.sin(lat),
            c[2]+r*math.cos(lat)*math.sin(lon)]

def saddle_pt(uv, u, v, c, r):
    u0,v0,u1,v1 = uv
    x = u0 + u*(u1-u0); y = v0 + v*(v1-v0)
    return [x+c[0], y+c[1], (x*x-y*y)+c[2]]

PT = {"sphere": sphere_pt, "saddle": saddle_pt}

def est_n(p, err):
    # BUG: purely local density -> neighbors disagree on shared edges
    return max(1, int(math.ceil(0.05 / err)))

def tessellate(sc):
    verts, tris, stats = [], [], []
    vmap = {}
    def vid(p):
        # BUG: exact-equality merge -> boundary duplicates persist
        k = (p[0], p[1], p[2])
        if k not in vmap:
            vmap[k] = len(verts); verts.append(list(p))
        return vmap[k]
    for idx in sc["order"]:
        p = sc["patches"][idx]
        n = est_n(p, sc["err"])
        grid = [[vid(PT[p["type"]](p["uv"], i/n, j/n, p.get("center",[0,0,0]), p.get("r",1)))
                 for j in range(n+1)] for i in range(n+1)]
        cnt = 0
        for i in range(n):
            for j in range(n):
                for tri in ([grid[i][j], grid[i+1][j], grid[i+1][j+1]],
                            [grid[i][j], grid[i+1][j+1], grid[i][j+1]]):
                    # BUG: no degenerate filter
                    tris.append(tri); cnt += 1
        stats.append({"id": idx, "edge_factors": {"N":n,"S":n,"E":n,"W":n}, "n_tris": cnt})
    dig = hashlib.sha256(repr([verts, tris]).encode()).hexdigest()[:16]
    return {"verts": verts, "tris": tris, "patch_stats": stats, "digest": dig}

def main():
    sc = json.load(open(sys.argv[1]))
    json.dump(tessellate(sc), open(sys.argv[2], "w"))

if __name__ == "__main__":
    main()
