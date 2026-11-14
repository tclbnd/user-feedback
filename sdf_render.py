#!/usr/bin/env python3
"""SDF CSG raymarcher (no backtracking baseline)."""
import json, sys, math, os

def prim_eval(node, p):
    k = node["prim"]
    if k == "sphere":
        c = node["c"]
        return math.dist(p, c) - node["r"]
    if k == "box":
        c, h = node["c"], node["h"]
        q = [abs(p[i]-c[i])-h[i] for i in range(3)]
        o = math.sqrt(sum(max(v,0)**2 for v in q))
        return o + min(0.0, max(q))
    if k == "plane":
        n = node["n"]
        return sum(p[i]*n[i] for i in range(3)) - node["d"]

def eval_tree(node, p, order="post"):
    if "prim" in node:
        return prim_eval(node, p)
    da = eval_tree(node["a"], p, order)
    db = eval_tree(node["b"], p, order)
    if order == "pre":     # BUG: different association -> different float bits
        da, db = db, da
    if node["op"] == "union": return min(da, db)
    if node["op"] == "inter": return max(da, db)
    if node["op"] == "sub":   return max(da, -db)

def march(tree, ro, rd, order):
    t, steps = 0.0, 0
    while t < 20 and steps < 256:
        p = [ro[i]+rd[i]*t for i in range(3)]
        d = eval_tree(tree, p, order)
        if d < 1e-4:
            return t, steps
        t += d; steps += 1      # BUG: no overshoot backtrack -> thin walls tunneled
    return None, steps

def render(sc):
    W, H = sc["res"]
    order = sc.get("eval_order", "post")
    img, steps_img, own = [], [], []
    hits = 0; hist = {}
    for j in range(H):
        row, srow = [], []
        for i in range(W):
            x = (2*i/W-1); y = (1-2*j/H)
            rd = [x, y, -2.0]
            n = math.sqrt(sum(v*v for v in rd)); rd = [v/n for v in rd]
            t, s = march(sc["tree"], [0,0,4], rd, order)
            row.append(255 if t else 0); srow.append(min(255, s))
            if t: hits += 1
            hist[s//8] = hist.get(s//8, 0) + 1
        img.append(row); steps_img.append(srow)
    return img, steps_img, {"hits": hits, "steps_hist": hist, "ownership": own}

def write_pgm(p, img):
    h, w = len(img), len(img[0])
    open(p,"w").write(f"P2\n{w} {h}\n255\n"+"\n".join(" ".join(map(str,r)) for r in img))

def main():
    sc = json.load(open(sys.argv[1])); out = sys.argv[2]
    os.makedirs(out, exist_ok=True)
    img, s, stats = render(sc)
    write_pgm(os.path.join(out,"img.pgm"), img)
    write_pgm(os.path.join(out,"steps.pgm"), s)
    json.dump(stats, open(os.path.join(out,"stats.json"),"w"))

if __name__ == "__main__":
    main()
