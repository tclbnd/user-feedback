import struct, subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
def run(args):
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y"]+args,capture_output=True,text=True,timeout=60)
    return r.returncode, r.stderr.strip()
# exact N=156000 from a0.pcm first 156000 samples
d=open("a0.pcm","rb").read()[:156000*2]
open("n156.pcm","wb").write(d)
run(["-loglevel","error","-f","s16le","-ar","48000","-ac","1","-i","n156.pcm","-c:a","aac","-b:a","160k","-f","mp4","n156.mp4"])
run(["-loglevel","error","-i","n156.mp4","-vn","-f","s16le","n156dec.pcm"])
print("enc file samples", os.path.getsize("n156dec.pcm")//2)
def peaks(path, thr):
    d=open(path,"rb").read(); n=len(d)//2
    s=struct.unpack("<%dh"%n,d)
    idx=[i for i,v in enumerate(s) if v>thr]
    g=[]
    for p in idx:
        if not g or p-g[-1][-1]>100: g.append([p])
        else: g[-1].append(p)
    return [(x[0],x[-1]) for x in g]
a=open("n156.pcm","rb").read(); n=len(a)//2
sa=struct.unpack("<%dh"%n,a)
print("input peak", max(sa))
print("input pulses>20000", peaks("n156.pcm",20000)[:6], "count", len(peaks("n156.pcm",20000)))
print("out peak", max(struct.unpack("<%dh"%(os.path.getsize('n156dec.pcm')//2), open('n156dec.pcm','rb').read())))
print("out pulses>20000", peaks("n156dec.pcm",20000)[:6], "count", len(peaks("n156dec.pcm",20000)))
for thr in [15000,12000,10000]:
    print(thr, "in", len(peaks("n156.pcm",thr)), "out", len(peaks("n156dec.pcm",thr)))
