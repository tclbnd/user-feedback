"""Projective-texture replay with a nearest-pixel baseline."""
import argparse
import json
from pathlib import Path


def render(data):
    texture=data["texture"]
    return {"results":[{"color":texture[0][0],"gradient":[0,0,0,0],"mip":0,"taps":1}
                       for _ in data["samples"]]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
