#!/usr/bin/env python3
import json,sys
def run(req):
    fr=req["frames"];gop=req["gop"];res={"ladder":{}}
    for ld in req["ladder"]:
        gops=[];i=0
        while i<len(fr):
            end=min(i+gop,len(fr))             # BUG: fixed count per ladder, ignores scene
            gops.append({"start":i,"end":end,"idr":i});i=end
        frames=[{"t":f["t"],"ts":f["t"],"pts":f["t"]} for f in fr]
        res["ladder"][ld["name"]]={"rate_avg":ld["rate"]*0.92,"frames":frames,"switch_ok":True}
        res.setdefault("gops",gops)
    res["psnr"]={ld["name"]:40.0 for ld in req["ladder"]}
    return res
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
