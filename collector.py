"""Baseline collector over the fixture source transport."""
import argparse
import json
from pathlib import Path
from source import Source


def inspect(root,version):
    state=root/"published.json"
    if not state.exists():
        return {"version":0,"token":None,"rows":[],"diff":{"insert":[],"update":[],"delete":[]}}
    return json.loads(state.read_text())


def collect(spec,root,max_requests,crash):
    root.mkdir(parents=True,exist_ok=True)
    src=Source(json.loads(spec.read_text()),root/"source-state.json")
    token=src.latest()
    cursor=None
    rows={}
    for _ in range(max_requests or 100):
        code,page=src.fetch(token,cursor)
        if code!=200:
            break
        for row in page["items"]:
            rows[row["id"]]=row
        cursor=page["next"]
        if cursor is None:
            break
    result={"version":inspect(root,None)["version"]+1,"token":token,
            "rows":sorted(rows.values(),key=lambda r:r["id"]),
            "diff":{"insert":[],"update":[],"delete":[]}}
    (root/"published.json").write_text(json.dumps(result)+"\n")
    return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("root",type=Path)
    sub=parser.add_subparsers(dest="op",required=True)
    sync=sub.add_parser("collect")
    sync.add_argument("spec",type=Path)
    sync.add_argument("--max-requests",type=int)
    sync.add_argument("--crash",choices=["after-stage","before-cursor","before-publish"])
    view=sub.add_parser("inspect")
    view.add_argument("--version",type=int)
    args=parser.parse_args()
    try:
        if args.op=="collect":
            result=collect(args.spec,args.root,args.max_requests,args.crash)
        else:
            result=inspect(args.root,args.version)
        print(json.dumps(result,sort_keys=True))
    except (ValueError,KeyError) as err:
        parser.exit(2,str(err)+"\n")


if __name__=="__main__":
    main()
