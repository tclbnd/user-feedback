"""Field-sequence replay with a fixed two-field weave."""
import argparse
import hashlib
import json
from pathlib import Path


def render(data):
    fields=data["fields"]
    frames=[]
    for i in range(0,len(fields)-1,2):
        a,b=fields[i:i+2]
        rows=[val for pair in zip(a["rows"],b["rows"]) for val in pair]
        frames.append({"mode":"progressive","field_ids":[a["id"],b["id"]],
                       "sha256":hashlib.sha256(bytes(v for row in rows for v in row)).hexdigest()})
    return {"frames":frames,"segments":[],"diagnostics":[]}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("input",type=Path)
    p.add_argument("output",type=Path)
    a=p.parse_args()
    a.output.write_text(json.dumps(render(json.loads(a.input.read_text())),sort_keys=True)+"\n")


if __name__=="__main__":
    main()
