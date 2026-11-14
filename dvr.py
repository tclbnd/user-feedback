#!/usr/bin/env python3
import json,sys
def run(req):
    manifest=[];visible=set()
    for s in range(req["segs"]):
        manifest.append(s)
        if s>=req["window"]: manifest.pop(0)
        if s%3==0: visible.add(s)                     # 部分片段"未就绪"
    dang=[s for s in manifest if s not in visible]
    return {"manifest":[{"seq":s,"visible":s in visible} for s in manifest],
            "dangling":len(dang),"recovered":False,
            "snapshots":{str(r):manifest[:] for r in range(req["readers"])}}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
