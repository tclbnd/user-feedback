"""Terrain replay with independent per-tile tessellation."""
import argparse
import json
import math
from pathlib import Path


def replay(data):
    result=[]
    for i,frame in enumerate(data["frames"]):
        patches=[]
        for tile in frame["tiles"]:
            x,y=tile["x"],tile["y"]
            n=8 if tile["error"]>=1 else 2
            patches.append({"id":tile["id"],"level":int(n==8),"triangles":2*n*n,
                            "bounds":[x,y,x+1,y+1],"edges":{}})
        result.append({"tiles":patches,"triangles":sum(x["triangles"] for x in patches)})
    return {"frames":result}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("scene",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(replay(json.loads(a.scene.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
