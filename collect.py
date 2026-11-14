#!/usr/bin/env python3
import json,sys,hashlib
def walk(node,out,depth=0):
    for f in node.get("fields",[]): out.append({"name":f["name"],"value":f.get("value")})
    for fr in node.get("frames",[]): walk(fr,out,depth+1)
def run(page):
    out=[]; walk(page,out)
    seen={}
    for f in out: seen[f["name"]]=f          # BUG: last wins, overwrites same-name fields
    return {"frames":[],"fields":list(seen.values()),
            "coverage":{"frames":0,"fields":len(seen)},"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
