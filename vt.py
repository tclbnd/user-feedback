#!/usr/bin/env python3
import json,sys
def run(req):
    res=[]
    faults=[]
    for f in req["frames"]:
        want=sorted({p["pid"] for p in f})[:req["phys"]]
        res.append(want);faults.append(len(want))       # BUG: 每帧全替换
    return {"resident":res,"faults":faults,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
