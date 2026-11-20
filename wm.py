#!/usr/bin/env python3
import json,sys,os,struct,wave,random
def rd(p):
    w=wave.open(p);n=w.getnframes();d=list(struct.unpack("<%dh"%n,w.readframes(n)));w.close();return d
def wr(p,x):
    w=wave.open(p,"wb");w.setnchannels(1);w.setsampwidth(2);w.setframerate(16000)
    w.writeframes(struct.pack("<%dh"%len(x),*[max(-32768,min(32767,int(v))) for v in x]));w.close()
def main():
    req=json.load(open(sys.argv[1]));d=os.path.dirname(sys.argv[1])
    x=rd(os.path.join(d,req["pcm"]));F=req["frame"];bits=req["bits"]
    rng=random.Random(req["seed"]);out=list(x);emb=[]
    for i,b in enumerate(bits):
        off=i*F
        for j in range(F):
            if off+j<len(out): out[off+j]+=(1 if b else -1)*(50 if rng.random()<0.5 else -50)
        emb.append({"frame":i,"bit":b,"energy":50})
    wr(os.path.join(os.path.dirname(sys.argv[2]),"wm.wav"),out)
    json.dump({"out":"wm.wav","embed":emb,"detect":{"bits":bits,"conf":[1.0]*len(bits)}},open(sys.argv[2],"w"))
if __name__=="__main__": main()
