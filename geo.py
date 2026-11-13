"""Local hourly telemetry store with a baseline linear range query."""
import argparse
import json
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def apply(root, path, crash):
    root.mkdir(parents=True,exist_ok=True)
    head=root/"points.json"
    existing=load(head) if head.exists() else {"version":0,"points":[]}
    state={"version":existing["version"]+1,"points":existing["points"]+load(path)["points"]}
    head.write_text(json.dumps(state)+"\n")
    return {"version":state["version"],"total_points":len(state["points"])}


def queries(root,path,version):
    state=load(root/"points.json")
    data=load(path)
    result=[]
    for q in data["queries"]:
        found=[p for p in state["points"]
               if q["start"]<=p["t"]<q["end"] and q["west"]<=p["lon"]<=q["east"]
               and q["south"]<=p["lat"]<=q["north"]]
        result.append({"id":q["id"],"rows":sorted(found,key=lambda p:(p["t"],p["device"])),
                       "candidate_blocks":1,"scanned_points":len(state["points"])})
    return {"version":state["version"],"results":result}


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    sub=parser.add_subparsers(dest="command",required=True)
    stage=sub.add_parser("apply")
    stage.add_argument("input",type=Path)
    stage.add_argument("--crash-before-publish",action="store_true")
    scan=sub.add_parser("query")
    scan.add_argument("input",type=Path)
    scan.add_argument("--version",type=int)
    args=parser.parse_args()
    if args.command=="apply":
        result=apply(args.root,args.input,args.crash_before_publish)
    else:
        result=queries(args.root,args.input,args.version)
    print(json.dumps(result,allow_nan=False,sort_keys=True))


if __name__=="__main__":
    main()
