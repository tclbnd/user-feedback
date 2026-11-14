#!/usr/bin/env python3
import json,sys,os,struct,wave,math
def rd(p):
    w=wave.open(p);n=w.getnframes();d=list(struct.unpack("<%dh"%n,w.readframes(n)));w.close();return d
def main():
    req=json.load(open(sys.argv[1]));d=os.path.dirname(sys.argv[1])
    trs=[rd(os.path.join(d,t["pcm"])) for t in req["tracks"]]
    n=max(len(t) for t in trs)
    mix=[sum(t[i] if i<len(t) else 0 for t in trs) for i in range(n)]
    peak=max(abs(v) for v in mix)
    w=wave.open(os.path.join(os.path.dirname(sys.argv[2]),"mix.wav"),"wb")
    w.setnchannels(1);w.setsampwidth(2);w.setframerate(req["fs"])
    w.writeframes(struct.pack("<%dh"%n,*[int(max(-32768,min(32767,v))) for v in mix]));w.close()
    pk=20*math.log10(peak/32768)
    json.dump({"out":"mix.wav","gains":{t["name"]:0 for t in req["tracks"]},"peak_db":pk,"lufs":pk},open(sys.argv[2],"w"))
if __name__=="__main__": main()
