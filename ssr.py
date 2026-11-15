#!/usr/bin/env python3
import json,sys,math
def run(req):
    D=req["depth"] if isinstance(req["depth"],list) else json.load(open(req["depth"]))
    hits=[]
    for i,r in enumerate(req["rays"]):
        t=0.0;hit=None
        for s in range(64):
            t+=2.0                                       # BUG: 固定大步长
            x=int(r["ox"]+r["od"][0]*t);y=int(r["oy"]+r["od"][1]*t)
            if not(0<=x<64 and 0<=y<64): hit={"src":"out"};break
            if D[y][x]<r["od"][2]*t: hit={"src":"march"};break
        hits.append({"ray_i":i,"t":t,"conf":1.0 if hit else 0,"src":(hit or {}).get("src","miss")})
    return {"hits":hits,"steps_hist":{},"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
