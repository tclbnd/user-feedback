#!/usr/bin/env python3
import json,sys
def run(req):
    alloc={};evict=[];casc={}
    for fi,f in enumerate(req["frames"]):
        ls=sorted(req["lights"],key=lambda l:l["id"])     # BUG: id 排序非优先级
        a={}
        for si,l in enumerate(ls[:req["slots"]]):
            a[l["id"]]=si%req["slots"]
        alloc[str(fi)]=a;casc[str(fi)]={l["id"]:0 for l in ls[:req["slots"]]}
    return {"alloc":alloc,"evict":evict,"cascade":casc,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
