"""Device timestamp alignment prototype."""
import argparse
import json
from pathlib import Path


def align(data):
    mapping=[]
    for key,device in sorted(data["devices"].items()):
        difference=(device["sync"][0]["reference"]-device["sync"][0]["tick"]) if device["sync"] else 0
        for p in device["probes"]:
            mapping.append({"device":key,"id":p["id"],"epoch":0,
                            "reference":p["tick"]+difference})
    return {"mapped":mapping,"epochs":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    result=align(json.loads(a.input.read_text()))
    a.output.write_text(json.dumps(result,allow_nan=False,sort_keys=True)+"\n")


if __name__=="__main__":
    main()
