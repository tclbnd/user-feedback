#!/usr/bin/env python3
import json,sys
def run(req):
    last=0;seq=0;ids=[];meta=[]
    for t in req["clock"]:
        ts=int(t*1000)
        if ts<=last: seq=0                              # BUG: allows going back
        ids.append(ts*4096+seq); meta.append({"ts":ts,"seq":seq,"src":"normal"})
        seq+=1; last=ts
    json.dump(req.get("state","/dev/null")=="x" and {} or {"last":last},open(req.get("state","st.json"),"w")) if req.get("state") else None
    return {"ids":ids,"meta":meta,"alerts":[]}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
