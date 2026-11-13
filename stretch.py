"""Two-channel speed replay with a nearest-sample baseline."""
import argparse
import json
from pathlib import Path


def render(data):
    frames=[frame for block in data["blocks"] for frame in block]
    speed=data["knots"][0][1]
    output=[frames[int(i*speed)] for i in range(int(len(frames)/speed))]
    return {"frames":output,"mapping":[],"transients":[],"latency":0}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),allow_nan=False)+"\n")


if __name__=="__main__":
    main()
