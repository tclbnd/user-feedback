#!/usr/bin/env python3
import json,sys,math
def run(req):
    S=req["size"];cov=[[0.0]*S for _ in range(S)]
    for s in req["segs"]:
        steps=int(math.hypot(s["x1"]-s["x0"],s["y1"]-s["y0"])*2)+1
        for i in range(steps+1):
            t=i/steps;x=s["x0"]+(s["x1"]-s["x0"])*t;y=s["y0"]+(s["y1"]-s["y0"])*t
            if s["w"]>=1:                                   # BUG: 亚像素宽直接跳过
                xi,yi=int(x),int(y)
                if 0<=xi<S and 0<=yi<S: cov[yi][xi]=1.0
    e=sum(sum(r) for r in cov)
    return {"cov":cov,"energy":e,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
