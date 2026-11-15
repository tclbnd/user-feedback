#!/usr/bin/env python3
import json,sys,math
def run(mesh):
    uv=[None]*len(mesh["pos"]);islands=0
    for ti,tri in enumerate(mesh["tris"]):
        for k,v in enumerate(tri):
            if uv[v] is None: uv[v]=[ti*0.1+k*0.05,ti*0.1]   # BUG: 每顶点首见投影
        islands+=1
    return {"uv":uv,"seams":[],"islands":islands,"energy":0,"angle_err":0,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
