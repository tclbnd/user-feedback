import base64
import hashlib
import json
from pathlib import Path


def piece(data,a,b,version):
    block=data[a:b]
    return {"version":version,"start":a,"end":b,"sha256":hashlib.sha256(block).hexdigest(),
            "payload":base64.b64encode(block).decode("ascii")}


def sample(generation,count=20):
    objects=[]
    expected={}
    for n in range(count):
        raw=bytes((n*15+j*31+j*j+len(generation))%256 for j in range(320+n*7))
        v=f"{generation}-{n}"
        segments=[piece(raw,a,b,v) for a,b in [(100,231),(0,143),(228,len(raw))]]
        if n%2==0:
            segments[1]["retry"]=segments[1]["payload"]
            segments[1]["payload"]="AA=="
        name=f"obj-{n:03}"
        objects.append({"id":name,"version":v,"length":len(raw),
                        "sha256":hashlib.sha256(raw).hexdigest(),"chunks":segments})
        expected[name]=raw
    return {"generation":generation,"objects":objects},expected


if __name__=="__main__":
    Path("demo.json").write_text(json.dumps(sample("first",3)[0])+"\n")
