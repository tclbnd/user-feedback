#!/usr/bin/env python3
import json,sys,random
def run(req):
    rng=random.Random(req["seed"]);n=req["n"];dt=1/60
    pos=[[rng.uniform(0,64),rng.uniform(0,64)] for _ in range(n)]
    vel=[[rng.uniform(-3,3),rng.uniform(-3,3)] for _ in range(n)]
    for _ in range(req["steps"]):
        order=list(range(n));rng.shuffle(order)          # BUG: 每步乱序遍历→力场积分顺序漂移
        for i in order:
            vel[i][0]+=req["force"][0]*dt;vel[i][1]+=req["force"][1]*dt
            pos[i][0]+=vel[i][0]*dt;pos[i][1]+=vel[i][1]*dt
            for k in (0,1):
                if pos[i][k]<0 or pos[i][k]>req["bounds"][1]:
                    vel[i][k]*=-0.5;pos[i][k]=min(max(pos[i][k],0),req["bounds"][1])
    blend=[sum(p[0] for p in pos)%1,0.3,0.4,0.8]
    return {"traj":pos,"blend":blend,"digest":"","order":[]}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
