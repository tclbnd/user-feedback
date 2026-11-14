import struct, subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
def run(args):
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y"]+args,capture_output=True,text=True,timeout=60)
    return r.returncode, r.stderr.strip()
# slow sawtooth, 40 samples per step, distinct per block
N=156000
pcm=bytearray(2*N)
for i in range(N):
    v=int(30000*( (i//40) % 20 )/20.0)
    struct.pack_into("<h",pcm,2*i,v)
open("ramp.pcm","wb").write(pcm)
rc,err=run(["-loglevel","error","-f","s16le","-ar","48000","-ac","1","-i","ramp.pcm","-c:a","aac","-b:a","160k","-f","mp4","ramp.mp4"])
print("enc",rc,err)
rc,err=run(["-loglevel","warning","-i","ramp.mp4","-vn","-f","s16le","rampdec.pcm"])
d=open("rampdec.pcm","rb").read(); n=len(d)//2
print("decoded",n,"delta",n-N)
s=struct.unpack("<%dh"%n,d)
# find best alignment
def blockvals(sig, off, count):
    return [sig[off+40*k+20] for k in range(count)]
for shift in [0]:
    pass
# correlate first decoded block index with input block index
for j in range(0,120):
    if s[j]>10:
        print("first nonzero at",j, s[j:j+8]); break

a=open("ramp.pcm","rb").read(); na=len(a)//2
sa=struct.unpack("<%dh"%na,a)
# compare shifts
import math
def mse(shift):
    tot=0; c=0
    for i in range(5000, n-5000, 13):
        j=i+shift
        if 0<=j<na:
            tot+=(s[i]-sa[j])**2; c+=1
    return tot/c
for shift in range(-6,7):
    print(shift, round(mse(shift),1))
