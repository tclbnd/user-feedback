#!/usr/bin/env python3
import json,sys,math,struct,wave,os
def biquad(gdb,fc=1000,q=1,fs=48000):
    A=10**(gdb/40);w=2*math.pi*fc/fs;a=math.sin(w)/(2*q)
    b0=1+a*A;b1=-2*math.cos(w);b2=1-a*A;a0=1+a/A;a1=-2*math.cos(w);a2=1-a/A
    return [b0/a0,b1/a0,b2/a0,a1/a0,a2/a0]
def main():
    req=json.load(open(sys.argv[1]));fs=req["fs"];n=req["blocks"]*req["bs"]
    c0=biquad(0);c1=biquad(req["auto"][0]["biquad"]["g_db"])
    at=int(req["auto"][0]["at"]*n)
    x=[int(8000*math.sin(2*math.pi*440*i/fs)) for i in range(n)]
    y=[];s=[0,0,0,0]
    for i,v in enumerate(x):
        c=c1 if i>=at else c0                      # BUG: 阶跃
        o=c[0]*v+c[1]*s[0]+c[2]*s[1]-c[3]*s[2]-c[4]*s[3]
        s=[v,s[0],o,s[2]];y.append(int(o))
    out=os.path.join(os.path.dirname(sys.argv[2]),"out.wav")
    w=wave.open(out,"wb");w.setnchannels(1);w.setsampwidth(2);w.setframerate(fs)
    w.writeframes(struct.pack("<%dh"%len(y),*y));w.close()
    json.dump({"out":"out.wav","coef_traj":[c0,c1],"settle_ms":0,"peak_db":0},open(sys.argv[2],"w"))
if __name__=="__main__": main()
