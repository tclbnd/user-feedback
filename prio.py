#!/usr/bin/env python3
import json,sys,time
def run(sig):
    now=sig.get("now",1_700_000_000); q=[]
    for p in sig["pages"]:
        days=(now-p.get("lastmod",now))/86400
        score=days*0.01+p.get("imp",0)              # BUG: 只看 lastmod+imp 缺 etag/hist/缺省规则
        q.append({"url":p["url"],"p":score,"reason":"lastmod"})
    q.sort(key=lambda x:-x["p"])
    b=sig["budget"]
    return {"queue":q[:b],"skipped":[x["url"] for x in q[b:]],"fast_lane":[],"coverage":0,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
