#!/usr/bin/env python3
import json,sys
def run(ops):
    data={};snaps={};live=0;alloc=free=0;peak=0;iters={};errs=[]
    for o in ops:
        if o["op"]=="put": data[o["k"]]=o["v"];alloc+=1;live+=1;peak=max(peak,live)
        elif o["op"]=="snap": snaps[o["id"]]=dict(data)    # BUG: snapshot shares live dict? actually copies — real bug: no refcount tracking & pages freed
        elif o["op"]=="release":
            snaps.pop(o["id"],None);live=max(0,live-2);free+=2   # BUG: frees regardless
        elif o["op"]=="iter":
            src=snaps.get(o.get("snap_id") or o.get("snap"),data)  # BUG: falls back to live
            iters[str(o.get("snap") or o.get("snap_id") or o["out"])]=sorted(src)
    return {"pages_live":live,"reclaimed":free,"iters":iters,"errors":errs,
            "stats":{"allocs":alloc,"frees":free,"peak":peak}}
def main():
    with open(sys.argv[1]) as source:
        request = json.load(source)
    events = request["ops"] if isinstance(request, dict) else request
    with open(sys.argv[2], "w") as target:
        json.dump(run(events), target)
if __name__=="__main__": main()
