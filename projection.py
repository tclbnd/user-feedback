"""Two-projector calibration replay with an unblended baseline."""
import argparse
import json
from pathlib import Path


def compose(data):
    pattern=data["pattern"]
    targets=[]
    for projector in data["projectors"]:
        values=[]
        for row in projector["pixels"]:
            values.append([pattern[min(len(pattern)-1,round(v))][min(len(pattern[0])-1,round(u))]
                           for u,v in row])
        targets.append({"id":projector["id"],"signal":values})
    return {"projectors":targets,"combined":pattern,"calibration":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    try:
        output=compose(json.loads(a.input.read_text()))
        a.output.write_text(json.dumps(output,sort_keys=True,allow_nan=False)+"\n")
    except (ValueError,IndexError,KeyError) as err:
        p.exit(2,str(err)+"\n")


if __name__=="__main__":
    main()
