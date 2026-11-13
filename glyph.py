"""Bounding-box glyph preview baseline."""
import argparse
import json
from pathlib import Path


def render(data):
    return {"glyphs":{g["id"]:{"atlas_rect":[0,0,0,0]} for g in data["glyphs"]},
            "images":[{"id":r["id"],"coverage":[[1]*r["width"] for _ in range(r["height"])]}
                      for r in data["renders"]]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
