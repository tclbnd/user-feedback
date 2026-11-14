import struct, subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
def run(args):
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y"]+args,capture_output=True,text=True,timeout=60)
    return r.returncode, r.stderr.strip()
for N in [156000, 50000]:
    pcm=bytearray(2*N)
    for j in range(100, N, 1000):
        struct.pack_into("<hhh", pcm, 2*j, 10000, 20000, 10000)
    open("t.pcm","wb").write(pcm)
    rc,err=run(["-loglevel","error","-f","s16le","-ar","48000","-ac","1","-i","t.pcm","-c:a","aac","-b:a","160k","-f","mp4","t.mp4"])
    rc2,err2=run(["-loglevel","debug","-i","t.mp4","-vn","-f","s16le","tdec.pcm"])
    n=os.path.getsize("tdec.pcm")//2
    print("N",N,"enc",rc,err,"dec",n,"delta",n-N)
    for line in err2.splitlines():
        if "skip" in line.lower() or "discard" in line.lower() or "edit list" in line.lower():
            print("   ",line)
