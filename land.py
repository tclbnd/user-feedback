"""Local object landing prototype."""
import argparse
import base64
import hashlib
import json
from pathlib import Path


def state(root, version=None):
    path=root/"current.json"
    return json.loads(path.read_text()) if path.exists() else {"generation":None,"published":[],"quarantine":[]}


def land(root,data,workers,crash):
    root.mkdir(parents=True,exist_ok=True)
    generation=data["generation"]
    dest=root/generation
    dest.mkdir(exist_ok=True)
    result={"generation":generation,"published":[],"quarantine":[]}
    for obj in data["objects"]:
        output=b"".join(base64.b64decode(chunk["payload"]) for chunk in obj["chunks"])
        (dest/obj["id"]).write_bytes(output)
        result["published"].append({"id":obj["id"],"length":len(output),
                                    "sha256":hashlib.sha256(output).hexdigest()})
    (root/"current.json").write_text(json.dumps(result)+"\n")
    return result


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    load=sub.add_parser("land")
    load.add_argument("manifest",type=Path)
    load.add_argument("--workers",type=int,default=3)
    load.add_argument("--crash",choices=["after-piece","after-range","before-pointer"])
    view=sub.add_parser("inspect")
    view.add_argument("--generation")
    a=p.parse_args()
    try:
        if a.op=="land":
            out=land(a.root,json.loads(a.manifest.read_text()),a.workers,a.crash)
        else:
            out=state(a.root,a.generation)
        print(json.dumps(out,sort_keys=True))
    except (OSError,ValueError,KeyError) as err:
        p.exit(2,str(err)+"\n")


if __name__=="__main__":
    main()
