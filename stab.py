#!/usr/bin/env python3
import json,sys
def run(req):
    g=sorted(req["gyro"],key=lambda x:x["t"])
    poses=[]
    for ft in req["frames"]:
        i=min(range(len(g)),key=lambda k:abs(g[k]["t"]-ft))
        poses.append(g[i]["wz"]*0.001)
    rms=(sum(p*p for p in poses)/len(poses))**0.5
    return {"poses":poses,"residual_rms":rms,"crop_ratio":0.0,"comp":[]}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
