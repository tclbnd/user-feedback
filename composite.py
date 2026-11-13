"""Baseline multisample transparent compositing pass."""
import argparse
import json
from pathlib import Path


def blend(src,dst):
    return [a+b*(1-src[3]) for a,b in zip(src,dst)]


def render(scene,tile,budget):
    width,height=scene["size"]
    count=scene["samples"]
    frame=[[scene["opaque"][y][x] for x in range(width)] for y in range(height)]
    out=[[[list(p["rgba"]) for p in samples] for samples in row] for row in frame]
    loaded=set()
    for f in scene["fragments"]:
        x,y,s=f["x"],f["y"],f["sample"]
        k=x,y,s
        if k in loaded:
            continue
        loaded.add(k)
        out[y][x][s]=blend(f["rgba"],out[y][x][s])
    return {"rgba":[[[sum(sample[c] for sample in pixel)/count for c in range(4)]
                    for pixel in row] for row in out],
            "stats":{"overflow_pixels":0,"peak_fragment_records":len(loaded)}}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("scene",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--tile",type=int,default=8)
    p.add_argument("--budget",type=int,default=64)
    a=p.parse_args()
    result=render(json.loads(a.scene.read_text()),a.tile,a.budget)
    a.output.write_text(json.dumps(result,allow_nan=False,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
