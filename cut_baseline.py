#!/usr/bin/env python3
import json,sys,os,struct,wave
def main():
    req=json.load(open(sys.argv[1]));d=os.path.dirname(sys.argv[1])
    w=wave.open(os.path.join(d,req["pcm"]));n=w.getnframes();fs=w.getframerate()
    x=struct.unpack("<%dh"%n,w.readframes(n));w.close()
    thr=10**(req["features"]["level_db"]/20)*32768
    keep=[];cur=None
    for i in range(0,n,fs//50):
        seg=x[i:i+fs//50];lvl=max(map(abs,seg),default=0)
        if lvl>thr:
            if cur is None: cur=i
        elif cur is not None: keep.append([cur/fs,i/fs]);cur=None
    if cur is not None: keep.append([cur/fs,n/fs])
    json.dump({"keep":[{"start":a,"end":b} for a,b in keep],"drop":[],"boundaries":[b for a,b in keep],"stats":{}},open(sys.argv[2],"w"))
if __name__=="__main__": main()
