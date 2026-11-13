"""Prototype history report with one active dimension row per entity."""
import argparse
import json
from pathlib import Path


def report(data, asof):
    facts={}
    dims={}
    for event in data["events"]:
        if event["system"]>asof:
            continue
        if event["type"]=="fact":
            facts[event["id"]]=event
        else:
            dims[event["entity"]]=event
    result=[]
    for id,fact in sorted(facts.items()):
        dim=dims.get(fact["entity"])
        result.append({"id":id,"fact_version":fact["version"],"entity":fact["entity"],
                       "at":fact["at"],"dimension_version":dim["version"] if dim else None,
                       "label":dim.get("label") if dim else None})
    return {"asof":asof,"rows":result}


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="op",required=True)
    for cmd in ("apply","query"):
        a=sub.add_parser(cmd)
        a.add_argument("state",type=Path)
        if cmd=="apply":
            a.add_argument("input",type=Path)
        else:
            a.add_argument("--asof",type=int,required=True)
    a=p.parse_args()
    try:
        state=json.loads(a.state.read_text()) if a.state.exists() else {"events":[]}
        if a.op=="apply":
            state["events"]+=json.loads(a.input.read_text())["events"]
            a.state.write_text(json.dumps(state)+"\n")
            result={"accepted":len(state["events"]),"affected":[]}
        else:
            result=report(state,a.asof)
        print(json.dumps(result,sort_keys=True))
    except (KeyError,ValueError) as e:
        p.exit(2,str(e)+"\n")


if __name__=="__main__":
    main()
