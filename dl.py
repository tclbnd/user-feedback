#!/usr/bin/env python3
import json,sys,os,hashlib
def main():
    req=json.load(open(sys.argv[1])); out=sys.argv[2]; d=os.path.dirname(sys.argv[1])
    src=os.path.join(d,req["source"]); data=open(src,'rb').read()
    n=req["chunk"]; blocks=[data[i:i+n] for i in range(0,len(data),n)]
    state={}
    if os.path.exists(req.get("state","/none")): state=json.load(open(req["state"]))
    got=[]; stats={"retries":0,"bytes":0}
    for i,b in enumerate(blocks):
        if str(i) in state: continue            # BUG: trusts state, never verifies sha
        got.append(b); stats["bytes"]+=len(b)
        st={"done":{str(i):True for i in range(len(got))}}
        json.dump(st,open(req["state"],"w"))
    open(out,"wb").write(b"".join(got))
    json.dump({"mode":"chunked","chunks":[],"stats":stats,"alerts":[]},open(out+".meta.json","w"))
if __name__=="__main__": main()
