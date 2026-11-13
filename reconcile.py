"""Local reconciliation prototype."""
import argparse
from collections import defaultdict
import json
from pathlib import Path


def compute(data):
    balances=defaultdict(float)
    for e in data["entries"]:
        if e.get("void"):
            continue
        balances[e["account"]] += float(e["amount"]) * (1 if e["side"]=="debit" else -1)
    return {"balances":[{"account":a,"value":v} for a,v in sorted(balances.items())]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    a=sub.add_parser("apply")
    a.add_argument("source",type=Path)
    a.add_argument("--crash-before-publish",action="store_true")
    q=sub.add_parser("inspect")
    q.add_argument("--version",type=int)
    opt=p.parse_args()
    opt.root.mkdir(parents=True,exist_ok=True)
    state=opt.root/"report.json"
    try:
        if opt.op=="apply":
            result=compute(json.loads(opt.source.read_text()))
            state.write_text(json.dumps(result)+"\n")
        else:
            result=json.loads(state.read_text()) if state.exists() else {"version":0}
        print(json.dumps(result,sort_keys=True))
    except (ValueError,OSError,KeyError) as err:
        p.exit(2,str(err)+"\n")


if __name__=="__main__":
    main()
