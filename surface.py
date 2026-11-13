"""Scalar field and coarse voxel-surface starter."""
import argparse
import json
import math
from pathlib import Path


def field(config,x,y,z):
    center=config.get("center",[0,0,0])
    x,y,z=x-center[0],y-center[1],z-center[2]
    r=math.sqrt(x*x+y*y+z*z)
    if config["field"]=="sphere":
        return r-config["radius"]
    if config["field"]=="shell":
        return max(r-config["outer"],config["inner"]-r)
    return math.hypot(math.hypot(x,y)-config["major"],z)-config["minor"]


def extract(config):
    n=8
    lo,hi=config["domain"]
    step=(hi-lo)/n
    vertices=[]
    ids={}
    faces=[]
    cells={(x,y,z) for x in range(n) for y in range(n) for z in range(n)
           if field(config,*(lo+(v+.5)*step for v in (x,y,z)))<0}
    patterns=[
        ((0,0,-1),((0,0,0),(0,1,0),(1,1,0),(1,0,0))),
        ((0,0,1),((0,0,1),(1,0,1),(1,1,1),(0,1,1))),
        ((0,-1,0),((0,0,0),(1,0,0),(1,0,1),(0,0,1))),
        ((0,1,0),((0,1,0),(0,1,1),(1,1,1),(1,1,0))),
        ((-1,0,0),((0,0,0),(0,0,1),(0,1,1),(0,1,0))),
        ((1,0,0),((1,0,0),(1,1,0),(1,1,1),(1,0,1))),
    ]
    for x,y,z in sorted(cells):
        for (dx,dy,dz),points in patterns:
            if (x+dx,y+dy,z+dz) in cells:
                continue
            ix=[]
            for a,b,c in points:
                p=(x+a,y+b,z+c)
                if p not in ids:
                    ids[p]=len(vertices)
                    vertices.append([lo+v*step for v in p])
                ix.append(ids[p])
            faces.extend([[ix[0],ix[1],ix[2]],[ix[0],ix[2],ix[3]]])
    return {"vertices":vertices,"faces":faces,"stats":{"leaf_depths":{"3":n**3}}}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(extract(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
