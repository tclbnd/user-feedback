import struct, subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
def run(args):
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y"]+args,capture_output=True,text=True,timeout=60)
    return r.returncode, r.stderr.strip()
for N in [1000, 1023, 1024, 1025, 2048, 5000, 10000, 20000, 25000]:
    pcm=bytearray(2*N)
    for j in range(0,N,80):
        struct.pack_into("<h",pcm,2*j,8000)
    open("x.pcm","wb").write(pcm)
    run(["-loglevel","error","-f","s16le","-ar","48000","-ac","1","-i","x.pcm","-c:a","aac","-b:a","160k","-f","mp4","x.mp4"])
    rc,err=run(["-loglevel","warning","-i","x.mp4","-vn","-f","s16le","xdec.pcm"])
    out=os.path.getsize("xdec.pcm")//2
    print("N",N,"decoded",out,"delta",out-N)
