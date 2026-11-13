"""Offline instance renderer with a deliberately coarse occlusion heuristic."""
import argparse
import json
import math
from pathlib import Path


def project(box, width, height, near):
    x0,y0,z0,x1,y1,z1=box
    if z1 < near:
        return None
    distances=[max(near,z0),z1]
    xs=[width/2+(width/2)*x/z for x in (x0,x1) for z in distances]
    ys=[height/2+(height/2)*y/z for y in (y0,y1) for z in distances]
    return min(xs),min(ys),max(xs),max(ys)


def render(instance_ids, instances, frame, width, height, near):
    depths=[[math.inf]*width for _ in range(height)]
    image=[[None]*width for _ in range(height)]
    for occ in frame["occluders"]:
        if not occ["opaque"]:
            continue
        x0,y0,x1,y1=occ["rect"]
        for y in range(max(0,y0),min(y1,height)):
            for x in range(max(0,x0),min(x1,width)):
                if occ["z"]<depths[y][x]:
                    depths[y][x]=occ["z"]
    for inst in sorted(instances,key=lambda i:i["id"]):
        if inst["id"] not in instance_ids:
            continue
        p=project(inst["box"],width,height,near)
        if p is None:
            continue
        a,b,c,d=p
        z=max(near,inst["box"][2])
        for y in range(max(0,math.floor(b)),min(height,math.ceil(d))):
            for x in range(max(0,math.floor(a)),min(width,math.ceil(c))):
                if a<=x+.5<c and b<=y+.5<d and z<depths[y][x]:
                    image[y][x]=inst["id"]
                    depths[y][x]=z
    return image


def draw(scene, no_cull, workers):
    width,height=scene["size"]
    near=scene["near"]
    result=[]
    for frame in scene["frames"]:
        instances=frame["instances"]
        near_occ=min((o["z"] for o in frame["occluders"]),default=math.inf)
        kept={i["id"] for i in instances if no_cull or i["box"][2]<=near_occ}
        batches={}
        for inst in instances:
            if inst["id"] in kept:
                batches.setdefault(inst["material"],[]).append(inst["id"])
        result.append({"kept":sorted(kept),"batches":[{"material":m,"ids":ids} for m,ids in batches.items()],
                       "ids":render(kept,instances,frame,width,height,near),
                       "stats":{"hierarchy_queries":0}})
    return {"frames":result}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("scene",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--workers",type=int,default=1)
    p.add_argument("--no-cull",action="store_true")
    a=p.parse_args()
    data=json.loads(a.scene.read_text())
    result=draw(data,a.no_cull,a.workers)
    a.output.write_text(json.dumps(result,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
