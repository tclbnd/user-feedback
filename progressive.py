"""Progressive mesh I/O with a one-level baseline."""
import argparse
import json
from pathlib import Path


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="op",required=True)
    e=sub.add_parser("encode")
    e.add_argument("mesh",type=Path)
    e.add_argument("stream",type=Path)
    d=sub.add_parser("decode")
    d.add_argument("stream",type=Path)
    d.add_argument("output",type=Path)
    d.add_argument("--level",type=int,default=0)
    a=p.parse_args()
    if a.op=="encode":
        data=json.loads(a.mesh.read_text())
        result={"base":data["mesh"],"splits":[],"levels":[{"count":len(data["mesh"]["faces"]),"split":0}]}
        target=a.stream
    else:
        result=json.loads(a.stream.read_text())["base"]
        target=a.output
    target.write_text(json.dumps(result,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
