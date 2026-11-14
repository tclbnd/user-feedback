#!/usr/bin/env python3
import json,sys
def run(req):
    D=req["shadow"]
    lit=[]
    for q in req["query"]:
        x,y,z=q["x"],q["y"],q["z"]
        occl=0;tot=0
        for j in range(max(0,y-8),min(64,y+8)):
            for i in range(max(0,x-8),min(64,x+8)):
                tot+=1
                if D[j][i]<z: occl+=1
        v=1.0-occl/max(1,tot)                           # BUG: 均匀模糊+固定核
        lit.append({"x":x,"y":y,"v":v,"kernel":8})
    return {"lit":lit,"avg_depth":{},"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
