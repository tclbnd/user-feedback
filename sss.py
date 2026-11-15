#!/usr/bin/env python3
import json,sys
def run(req):
    N=req["normals"];L=req["light_dir"]
    trans=[[max(0.0,-sum(N[j][i][k]*L[k] for k in range(3)))*0.5 for i in range(32)] for j in range(32)]
    return {"trans":trans,"curv":[[0]*32 for _ in range(32)],"dark_zero":False,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
