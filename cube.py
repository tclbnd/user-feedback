"""Directional lookup of a six-face scalar linear-light cubemap."""
import argparse
import json
import math
from pathlib import Path

FACES=("+x","-x","+y","-y","+z","-z")


def direction(face,u,v):
    return {
        "+x":(1,-v,-u),"-x":(-1,-v,u),"+y":(u,1,v),
        "-y":(u,-1,-v),"+z":(u,-v,1),"-z":(-u,-v,-1),
    }[face]


def locate(d):
    x,y,z=d
    if abs(x)>=max(abs(y),abs(z)):
        return ("+x",-z/x,-y/x) if x>0 else ("-x",z/-x,y/x)
    if abs(y)>=abs(z):
        return ("+y",x/y,z/y) if y>0 else ("-y",x/-y,z/y)
    return ("+z",x/z,-y/z) if z>0 else ("-z",x/z,y/z)


def sample(faces,d):
    face,u,v=locate(d)
    grid=faces[face]
    n=len(grid)
    i=max(0,min(n-1,int((u+1)*n/2)))
    j=max(0,min(n-1,int((v+1)*n/2)))
    return grid[j][i]


def run(data):
    faces=data["faces"]
    samples=[sample(faces,d) for d in data["directions"]]
    levels=[]
    for r in data["roughness"]:
        levels.append({"roughness":r,"samples":samples})
    return {"samples":samples,"levels":levels}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("input",type=Path)
    parser.add_argument("output",type=Path)
    args=parser.parse_args()
    data=json.loads(args.input.read_text())
    args.output.write_text(json.dumps(run(data),sort_keys=True,allow_nan=False)+"\n")


if __name__=="__main__":
    main()
