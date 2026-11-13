"""Vector rasterizer front end with a straight-edge baseline."""
import argparse
import json
from pathlib import Path


def polygon(commands):
    return [tuple(command[-2:]) for command in commands if command[0] in ("M","L","Q","C")]


def inside(points,x,y):
    hits=0
    for a,b in zip(points,points[1:]+points[:1]):
        if (a[1]>y)!=(b[1]>y):
            hits += (a[0]+(b[0]-a[0])*(y-a[1])/(b[1]-a[1]))>x
    return hits%2


def render(scene,tile):
    w,h=scene["size"]
    ox,oy=scene.get("origin",[0,0])
    points=polygon(scene["shape"]["commands"])
    mask=[[int(inside(points,ox+x+.5,oy+y+.5)) for x in range(w)] for y in range(h)]
    rgba=[[scene["color"] if p else [0,0,0,0] for p in row] for row in mask]
    return {"mask":mask,"rgba":rgba}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("output",type=Path)
    parser.add_argument("--tile",type=int,default=8)
    args=parser.parse_args()
    result=render(json.loads(args.input.read_text()),args.tile)
    args.output.write_text(json.dumps(result,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
