#!/usr/bin/env python3
import json,sys
def run(req):
    op=sorted(req["opaque"],key=lambda o:o["z"])
    base=op[0]["albedo"] if op else [0.1,0.1,0.1]
    c=list(base)+[1.0]
    for t in req["trans"]:                               # BUG: 提交顺序over混合
        c=[t["tint"][k]*t["alpha"]+c[k]*(1-t["alpha"]) for k in range(3)]+[1.0]
    return {"color":c,"per_light":[],"occ":0,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
