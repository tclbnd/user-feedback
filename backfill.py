"""Local partition backfill prototype."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    sync=sub.add_parser("build")
    sync.add_argument("source",type=Path)
    sync.add_argument("--crash-after-layer",type=int)
    sub.add_parser("inspect")
    a=p.parse_args()
    a.root.mkdir(parents=True,exist_ok=True)
    state=a.root/"published.json"
    if a.op=="build":
        config=json.loads(a.source.read_text())
        inputs=config["inputs"]
        result={"values":dict(inputs),"executed":sorted(inputs),"fingerprints":{}}
        state.write_text(json.dumps(result)+"\n")
    else:
        result=json.loads(state.read_text()) if state.exists() else {"values":{},"fingerprints":{}}
    print(json.dumps(result,sort_keys=True))


if __name__=="__main__":
    main()
