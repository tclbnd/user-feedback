"""Baseline daily summary prototype."""
import argparse
import json
import math
from pathlib import Path


def percent(values,p):
    return sorted(values)[math.ceil(len(values)*p)-1] if values else None


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    a=sub.add_parser("apply")
    a.add_argument("input",type=Path)
    q=sub.add_parser("query")
    q.add_argument("--start",type=int,required=True)
    q.add_argument("--end",type=int,required=True)
    q.add_argument("--reverse",action="store_true")
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    state=a.root/"partitions.json"
    current=json.loads(state.read_text()) if state.exists() else {}
    if a.op=="apply":
        for v in json.loads(a.input.read_text())["partitions"]:
            current[str(v["index"])]=v
        state.write_text(json.dumps(current)+"\n")
        result={"recomputed":[]}
    else:
        parts=[d for k,d in current.items() if a.start<=int(k)<a.end and d.get("values")]
        result={"count":sum(len(r["values"]) for r in parts),
                "quantiles":{key:sum(percent(r["values"],rank) for r in parts)/len(parts) if parts else None
                             for key,rank in (("50",.5),("95",.95),("99",.99))}}
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
