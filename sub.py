#!/usr/bin/env python3
import json,sys
def run(req):
    frames=[]
    for f in range(int(req["dur"]*req["fps"])):
        t=f/req["fps"]
        rects=[{"text":s["text"],"x":10,"y":200,"w":len(s["text"])*8,"h":req["font"]}
               for s in req["subs"] if s["t0"]<=t<s["t1"]]
        frames.append({"f":f,"rects":rects})
    return {"frames":frames,"degrade":0,"overlap_px":0}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
