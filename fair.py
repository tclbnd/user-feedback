#!/usr/bin/env python3
import json,sys
def run(sim):
    jobs={j["id"]:dict(j,done=0,start=None,pre=0) for j in sim["jobs"]}
    events=[]; bills={}; t=0; run=None; finish=[]
    while t<=sim["horizon"]:
        arr=[j for j in sim["jobs"] if j["arrive"]==t]
        if arr and (run is None or arr[0]["w"]>jobs[run]["w"]):
            if run:
                bills[run]=t-jobs[run]["start"]          # BUG: full span billed
                jobs[run]["pre"]+=1
            run=arr[0]["id"]; jobs[run]["start"]=t
            events.append({"t":t,"job":run,"kind":"start"})
        if run:
            jobs[run]["done"]+=1
            if jobs[run]["done"]>=jobs[run]["cost"]:
                bills[run]=t-jobs[run]["start"]+1
                events.append({"t":t,"job":run,"kind":"finish"});finish.append(run);run=None
        t+=1
    return {"events":events,"bills":bills,"preempts":{j:jobs[j]["pre"] for j in jobs},
            "finish_order":finish,"util":0}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
