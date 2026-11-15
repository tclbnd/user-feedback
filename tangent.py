#!/usr/bin/env python3
import json,sys
def run(mesh):
    P=mesh["pos"];UV=mesh["uv"];n=len(P)
    T=[[0.0,0,0] for _ in range(n)]
    for tri in mesh["tris"]:
        a,b,c=tri
        e=[P[b][k]-P[a][k] for k in range(3)]
        duv=[UV[b][k]-UV[a][k] for k in range(2)]
        d=duv[0] or 1e-9
        t=[e[k]/d for k in range(3)]
        for i in tri:
            for k in range(3): T[i][k]+=t[k]            # BUG: 顶点平均→镜像岛抵消
    tang=[]
    for i in range(n):
        L=sum(v*v for v in T[i])**0.5 or 1
        tang.append([T[i][0]/L,T[i][1]/L,T[i][2]/L,1.0])   # BUG: w 恒正→旋向丢失
    return {"tangents":tang,"degenerate":0,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
