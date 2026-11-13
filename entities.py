"""Entity link grouping prototype."""
import argparse
import json
from pathlib import Path


def group(nodes, links):
    result={n:{n} for n in nodes}
    for link in links:
        if link["action"]!="set" or link["kind"]!="same":
            continue
        a,b=link["a"],link["b"]
        block=result[a]|result[b]
        for x in block:
            result[x]=block
    return [{"id":f"entity-{key}","members":sorted(values)}
            for key,values in sorted(result.items()) if key==min(values)]


def main():
    p=argparse.ArgumentParser()
    sub=p.add_subparsers(dest="op",required=True)
    a=sub.add_parser("apply")
    a.add_argument("db",type=Path)
    a.add_argument("input",type=Path)
    a.add_argument("--upto",type=int)
    q=sub.add_parser("inspect")
    q.add_argument("db",type=Path)
    q.add_argument("--version",type=int)
    opt=p.parse_args()
    if opt.op=="apply":
        data=json.loads(opt.input.read_text())
        links=data["links"][:opt.upto]
        out={"version":len(links),"clusters":group(data["nodes"],links),"aliases":[],"changes":[]}
        opt.db.write_text(json.dumps(out)+"\n")
    else:
        out=json.loads(opt.db.read_text())
    print(json.dumps(out,sort_keys=True))


if __name__=="__main__":
    main()
