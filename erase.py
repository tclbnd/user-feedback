"""Synthetic customer read model and a flawed snapshot restore."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    for op in ("apply","restore"):
        a=sub.add_parser(op)
        if op=="apply":
            a.add_argument("input",type=Path)
        else:
            a.add_argument("version",type=int)
        a.add_argument("--crash-before-publish",action="store_true")
    sub.add_parser("inspect")
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    head=a.root/"current.json"
    state=json.loads(head.read_text()) if head.exists() else {"version":0,"customers":[],"purchases":[]}
    if a.op=="apply":
        data=json.loads(a.input.read_text())
        for event in data["events"]:
            if event["type"]=="customer":
                state["customers"].append(event)
            elif event["type"]=="purchase":
                state["purchases"].append(event)
            else:
                state["customers"]=[r for r in state["customers"] if r["key"]!=event["key"]]
        state["version"]+=1
    if a.op=="restore":
        state=json.loads((a.root/f'v{a.version}.json').read_text())
    if a.op!="inspect":
        raw=json.dumps(state)+"\n"
        (a.root/f'v{state["version"]}.json').write_text(raw)
        head.write_text(raw)
    print(json.dumps(state,sort_keys=True))


if __name__=="__main__":
    main()
