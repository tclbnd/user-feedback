#!/usr/bin/env python3
import json,sys
def run(flow,script):
    subs=[];skipped=[];diffs=[]
    for i,st in enumerate(script["steps"]):
        page=flow["pages"][i] if i<len(flow["pages"]) else None   # BUG: index定位
        payload={}
        for f in (page or {}).get("fields",[]):
            if f["name"] in st["fill"]: payload[f["name"]]=st["fill"][f["name"]]
        subs.append({"step":i,"fp":(page or {}).get("fp"),"payload":payload})
    return {"submitted":subs,"skipped":skipped,"aborted_at":None,"field_diff":diffs,
            "final_payload":{k:v for s in subs for k,v in s["payload"].items()}}
def main(): json.dump(run(json.load(open(sys.argv[1])),json.load(open(sys.argv[2]))),open(sys.argv[3],"w"))
if __name__=="__main__": main()
