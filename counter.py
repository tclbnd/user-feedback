"""Telemetry rollup prototype with JSON state and an offline input."""
import argparse
import json
from pathlib import Path


def inspect(db, version=None):
    if db.exists():
        return json.loads(db.read_text())
    return {"version":0,"intervals":[],"hourly":[],"changes":[]}


def apply(source, db, upto=None, crash=False):
    data=json.loads(source.read_text())
    latest={}
    intervals=[]
    hourly={}
    for sample in data["samples"][:upto]:
        name=sample["device"]
        old=latest.get(name)
        if old and sample["t"]>old["t"]:
            delta=max(0,sample["value"]-old["value"])
            intervals.append({"device":name,"start":old["t"],"end":sample["t"],
                              "kind":"normal","delta":delta})
            key=(name,sample["t"]//3600)
            hourly[key]=hourly.get(key,0)+delta
        latest[name]=sample
    result={"version":len(data["samples"][:upto]),"intervals":intervals,"changes":[],
            "hourly":[{"device":d,"hour":h,"delta":v} for (d,h),v in sorted(hourly.items())]}
    db.write_text(json.dumps(result)+"\n")
    return result


def main():
    parser=argparse.ArgumentParser()
    sub=parser.add_subparsers(dest="action",required=True)
    sync=sub.add_parser("apply")
    sync.add_argument("source",type=Path)
    sync.add_argument("state",type=Path)
    sync.add_argument("--upto",type=int)
    sync.add_argument("--crash-before-publish",action="store_true")
    view=sub.add_parser("inspect")
    view.add_argument("state",type=Path)
    view.add_argument("--version",type=int)
    args=parser.parse_args()
    if args.action=="apply":
        out=apply(args.source,args.state,args.upto,args.crash_before_publish)
    else:
        out=inspect(args.state,args.version)
    print(json.dumps(out,sort_keys=True,allow_nan=False))


if __name__=="__main__":
    main()
