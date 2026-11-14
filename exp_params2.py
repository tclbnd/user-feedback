import subprocess, os
from inspect_mp4 import track_infos, samples_from_info

FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"

def nals(payload):
    out=[]; pos=0
    while pos+4<=len(payload):
        n=int.from_bytes(payload[pos:pos+4],"big"); pos+=4
        if n<=0 or pos+n>len(payload): break
        out.append(payload[pos:pos+n]); pos+=n
    return out

def x264_params(path):
    data, infos = track_infos(path)
    v=[i for i in infos if i.get("handler")=="vide"][0]
    s=samples_from_info(data,v)[0]
    for nal in nals(data[s["offset"]:s["offset"]+s["size"]]):
        if nal[0]&31==6:
            i=nal.find(b"options: ")
            if i>=0:
                txt=nal[i+9:].split(b"\x00")[0].decode()
                return txt
    return None

def avcc(path):
    data, infos = track_infos(path)
    v=[i for i in infos if i.get("handler")=="vide"][0]
    return v["avcC"].hex()

def decode(src, out):
    subprocess.run([FF,"-hide_banner","-nostdin","-y","-loglevel","error","-i",src,"-map","0:v",
                    "-fps_mode","passthrough","-f","rawvideo","-pix_fmt","yuv420p",out],
                   check=True, timeout=120)

def encode(raw, params, out):
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y","-loglevel","error",
                      "-f","rawvideo","-pixel_format","yuv420p","-video_size","64x48","-framerate","20","-i",raw,
                      "-c:v","libx264","-preset","veryfast","-threads","1","-x264-params",params,
                      "-video_track_timescale","1000","-an",out],capture_output=True,text=True,timeout=120)
    return r.returncode, r.stderr.strip()[-400:]

for s in ["video0.mp4","video1.mp4","video2.mp4"]:
    params=x264_params(s)
    raw=s.replace(".mp4",".dec.yuv")
    decode(s,raw)
    fs=64*48*3//2
    d=open(raw,"rb").read()
    # re-encode a middle run of 9 frames with forced closed GOP for open-gop source
    run=s.replace(".mp4",".run.yuv")
    open(run,"wb").write(d[fs*10:fs*19])
    p2=params
    if "open_gop=1" in p2:
        p2=p2.replace("open_gop=1","open_gop=0")
    rc,err=encode(run,p2,s.replace(".mp4",".enc.mp4"))
    print(s,"rc",rc,"err",err)
    print("  src",avcc(s))
    print("  enc",avcc(s.replace(".mp4",".enc.mp4")))
    print("  equal", avcc(s)==avcc(s.replace(".mp4",".enc.mp4")))
