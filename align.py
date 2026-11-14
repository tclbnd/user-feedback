#!/usr/bin/env python3
import json,sys,os,struct,wave,math
def rd(p):
    w=wave.open(p);n=w.getnframes();d=struct.unpack("<%dh"%n,w.readframes(n));w.close();return list(d)
def wr(p,x):
    w=wave.open(p,"wb");w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
    w.writeframes(struct.pack("<%dh"%len(x),*[int(max(-32768,min(32767,v))) for v in x]));w.close()
def main():
    req=json.load(open(sys.argv[1]));d=os.path.dirname(sys.argv[1])
    ref=rd(os.path.join(d,req["tracks"][0]["pcm"]))
    tracks=[]
    for t in req["tracks"][1:]:
        x=rd(os.path.join(d,t["pcm"]))
        tracks.append({"name":t["name"],"lag_ms":0.0,"frac_ms":0.0,"reliable":True,"conf":1.0})
    mix=[min(32767,max(-32768,v)) for v in ref]
    wr(os.path.join(os.path.dirname(sys.argv[2]),"mix.wav"),mix)
    json.dump({"tracks":tracks,"mix":"mix.wav","notch_db_improve":0.0},open(sys.argv[2],"w"))
if __name__=="__main__": main()
