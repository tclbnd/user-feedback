import struct, subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
def run(args):
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y"]+args,capture_output=True,text=True,timeout=60)
    return r.returncode, r.stderr.strip()
N=200000
pcm=bytearray(2*N)
for j in range(0,N,997):
    struct.pack_into("<h",pcm,2*j,8000)
open("long.pcm","wb").write(pcm)
# encode with -t 3.25 s = 156000 samples
rc,err=run(["-loglevel","error","-f","s16le","-ar","48000","-ac","1","-i","long.pcm","-t","3.25","-c:a","aac","-b:a","160k","-movflags","+use_metadata_tags","t3250.mp4"])
print("enc",rc,err)
run(["-loglevel","error","-i","t3250.mp4","-vn","-f","s16le","t3250dec.pcm"])
print("decoded", os.path.getsize("t3250dec.pcm")//2)
