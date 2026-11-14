#!/usr/bin/env python3
import json,sys,hashlib
def h(x): return hashlib.sha256(json.dumps(x,sort_keys=True).encode()).hexdigest()[:12]
def run(old,new):
    fo,fn=h(old),h(new)
    cls="none" if fo==fn else "content"
    return {"fp_struct":fo,"fp_content":fn,"class":cls,
            "refetch":cls!="none","confidence":1.0,"evidence":{},"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1])),json.load(open(sys.argv[2]))),open(sys.argv[3],"w"))
if __name__=="__main__": main()
