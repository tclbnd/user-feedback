#!/usr/bin/env python3
import json,sys
def run(req):
    I=req["hdr"];n=len(I);avg=sum(sum(r) for r in I)/(n*n)
    out=[[v/(1+v) for v in row] for row in I]
    return {"ev":-avg,"out":out,"zone_ev":[],"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
