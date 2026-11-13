"""Minimal batch client prototype."""
import argparse
import json
from pathlib import Path


def initial():
    return {"leases":{},"checkpoints":{},"acks":{},"sink":[],"rejects":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    a=sub.add_parser("apply")
    a.add_argument("input",type=Path)
    a.add_argument("--lease",type=int,required=True)
    a.add_argument("--crash",choices=["after-outbox","after-sink","after-checkpoint","after-ack"])
    q=sub.add_parser("lease")
    q.add_argument("partition")
    q.add_argument("generation",type=int)
    sub.add_parser("inspect")
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    path=a.root/"state.json"
    state=json.loads(path.read_text()) if path.exists() else initial()
    if a.op=="lease":
        state["leases"][a.partition]=a.generation
    elif a.op=="apply":
        data=json.loads(a.input.read_text())
        for row in data["events"]:
            state["sink"].append(row)
            state["checkpoints"][row["partition"]]=row["offset"]
    if a.op!="inspect":
        path.write_text(json.dumps(state)+"\n")
    print(json.dumps(state,sort_keys=True))


if __name__=="__main__":
    main()
