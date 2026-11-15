#!/usr/bin/env python3
import json,sys,math,random
def run(req):
    rng=random.Random(req["seed"]);n=req["n"]
    acc=0.0
    for _ in range(n):
        z=rng.random();r=math.sqrt(z)                       # 半球均匀(非余弦)
        acc+=r*0.5                                          # BUG: 无 pdf/MIS
    return {"energy_err":abs(acc/n-0.5),"mis_weight":1.0,
            "counts":{"uniform":n},"replayable":False,"digest":""}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
