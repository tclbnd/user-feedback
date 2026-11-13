"""Business-date summary prototype."""
import argparse
from collections import Counter
import json
from pathlib import Path


def apply(data):
    counts=Counter(row["local"][:10] for row in data["events"] if not row.get("void"))
    return {"version":1,"days":[{"date":day,"count":n} for day,n in sorted(counts.items())],
            "rejected":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("state",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    a=sub.add_parser("apply")
    a.add_argument("input",type=Path)
    q=sub.add_parser("inspect")
    q.add_argument("--version",type=int)
    opt=p.parse_args()
    try:
        if opt.op=="apply":
            result=apply(json.loads(opt.input.read_text()))
            opt.state.write_text(json.dumps(result)+"\n")
        else:
            result=json.loads(opt.state.read_text())
        print(json.dumps(result,sort_keys=True))
    except (OSError,ValueError,KeyError) as err:
        p.exit(2,str(err)+"\n")


if __name__=="__main__":
    main()
