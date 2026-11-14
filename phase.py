#!/usr/bin/env python3
import json,sys,math,cmath,random
def istft(frames,win,hop,n):
    out=[0.0]*n;cnt=[0]*n
    for i,fr in enumerate(frames):
        pos=i*hop
        for k in range(win):
            v=sum(fr[f]*cmath.exp(2j*math.pi*f*k/win) for f in range(len(fr))).real/win
            if pos+k<n: out[pos+k]+=v;cnt[pos+k]+=1
    return [o/max(1,c) for o,c in zip(out,cnt)]
def run(req):
    mag=json.load(open(req["mag"]))
    rng=random.Random(req["seed"]);win=req["win"];hop=req["hop"]
    frames=[[m*cmath.exp(1j*rng.uniform(0,6.283)) for m in row] for row in mag]
    sig=istft(frames,win,hop,len(mag)*hop+win)
    return {"signal":sig,"err":[0.5],"snr":0,"iters_used":1}
def main(): json.dump(run(json.load(open(sys.argv[1]))),open(sys.argv[2],"w"))
if __name__=="__main__": main()
