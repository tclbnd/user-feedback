"""RGB-only spectral scene prototype."""
import argparse
import json
from pathlib import Path


def render(config):
    rows=[]
    for ray in config["rays"]:
        rows.append({"transmission":[1]*len(config["wavelengths"]),
                     "exit_angles":[ray["angle"]]*len(config["wavelengths"]),
                     "xyz":[1,1,1]})
    return {"rays":rows,"image":[r["xyz"] for r in rows],"stats":{}}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    p.add_argument("--tile",type=int,default=4)
    p.add_argument("--workers",type=int,default=1)
    a=p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
