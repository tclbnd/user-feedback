"""Prototype chart atlas builder with an isolated-level JSON interface."""
import argparse
import json
from pathlib import Path


def bake(data):
    charts={}
    for p in data["charts"]:
        n=p["size"]
        charts[p["id"]]=[{"size":n,"light":[[p.get("ambient",0)]*n for _ in range(n)]}]
    return {"charts":charts,"atlas":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(bake(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
