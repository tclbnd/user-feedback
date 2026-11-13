"""Raw PCM waveform preview with an unindexed EDL baseline."""
import argparse
import array
import hashlib
import json
import math
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text())


def write(path,obj):
    path.write_text(json.dumps(obj,sort_keys=True,allow_nan=False)+"\n")


def build(root,source,channels):
    root.mkdir(parents=True,exist_ok=True)
    data=source.read_bytes()
    frames=len(data)//(channels*2)
    write(root/"meta.json",{"source":str(source.resolve()),"channels":channels,
                            "frames":frames,"digest":hashlib.sha256(data).hexdigest()})
    write(root/"edl.json",{"version":1,"segments":[{"start":0,"end":frames}]})
    return {"version":1,"frames":frames}


def edit(root,edl,crash):
    state=read(root/"edl.json")
    segments=read(edl)["segments"]
    write(root/"edl.json",{"version":state["version"]+1,"segments":segments})
    return {"version":state["version"]+1}


def query(root,path,version):
    meta,state=read(root/"meta.json"),read(root/"edl.json")
    source=Path(meta["source"]).read_bytes()
    values=array.array("h")
    values.frombytes(source)
    if sys.byteorder!="little":
        values.byteswap()
    n=meta["channels"]
    pcm=[]
    for segment in state["segments"]:
        if "silence" in segment:
            pcm.extend([0]*(n*segment["silence"]))
        else:
            pcm.extend(values[segment["start"]*n:segment["end"]*n])
    output=[]
    for q in read(path)["ranges"]:
        rows=[]
        for ch in range(n):
            samples=pcm[q["start"]*n+ch:q["end"]*n:n]
            rows.append({"min":min(samples,default=0),"max":max(samples,default=0),
                         "rms":math.sqrt(sum(i*i for i in samples)/len(samples)) if samples else 0})
        output.append({"id":q["id"],"channels":rows})
    return {"version":state["version"],"ranges":output,"raw_bytes":len(source),"index_pages":0}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("root",type=Path)
    sub=p.add_subparsers(dest="op",required=True)
    b=sub.add_parser("build")
    b.add_argument("source",type=Path)
    b.add_argument("--channels",type=int,required=True)
    e=sub.add_parser("edit")
    e.add_argument("edl",type=Path)
    e.add_argument("--crash-before-publish",action="store_true")
    q=sub.add_parser("query")
    q.add_argument("ranges",type=Path)
    q.add_argument("--version",type=int)
    a=p.parse_args()
    try:
        if a.op=="build":
            o=build(a.root,a.source,a.channels)
        elif a.op=="edit":
            o=edit(a.root,a.edl,a.crash_before_publish)
        else:
            o=query(a.root,a.ranges,a.version)
        print(json.dumps(o,sort_keys=True))
    except (ValueError,OSError) as error:
        p.exit(2,str(error)+"\n")


if __name__=="__main__":
    main()
