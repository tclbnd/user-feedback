import json
import os
from pathlib import Path
import struct
import subprocess
import tempfile
import wave


def make(root):
    ff=os.environ.get("ZQ_FFMPEG","ffmpeg")
    root.mkdir(parents=True,exist_ok=True)
    files={}
    with tempfile.TemporaryDirectory(prefix="zq-cut-src-") as raw:
        temp=Path(raw)
        for idx in range(3):
            frames=bytearray()
            for i in range(65):
                marker=idx*100+i
                y=bytearray(38+(i*5+idx*13+x//5+y//8)%145 for y in range(48) for x in range(64))
                for row in range(6):
                    for x in range(36):
                        y[row*64+x]=230 if marker & (1<<(x//3)) else 22
                frames.extend(y)
                frames.extend(bytes([126+(idx+i)%6])*(64*48//2))
            src=temp/"input.yuv"
            src.write_bytes(frames)
            snd=temp/"audio.wav"
            pcm=bytearray(156000*2)
            for j in range(4410+idx*31,155900,3593):
                struct.pack_into("<hhh",pcm,2*j,16000,30000,15500)
            with wave.open(str(snd),"wb") as w:
                w.setparams((1,2,48000,156000,"NONE","not compressed"))
                w.writeframes(pcm)
            name=f"video{idx}"
            files[name]=name+".mp4"
            command=[ff,"-hide_banner","-loglevel","error","-nostdin","-y","-f","rawvideo",
                     "-pixel_format","yuv420p","-video_size","64x48","-framerate","20",
                     "-i",str(src),"-i",str(snd),"-vf","select='not(eq(mod(n,9),6))'",
                     "-fps_mode","vfr","-c:v","libx264","-preset","veryfast","-threads","1",
                     "-x264-params",f"keyint=12:min-keyint=12:scenecut=0:open-gop={int(idx==2)}:bframes=2",
                     "-video_track_timescale","1000","-c:a","aac","-b:a","160k",str(root/files[name])]
            subprocess.run(command,check=True)
    timings=[(100,800),(50,1250),(300,1100),(700,1850),(0,1000),(750,1950),
             (1000,1950),(0,2500),(1300,2300)]
    edl={"sources":files,"clips":[{"source":f"video{i%3}","start_ms":a,"end_ms":b}
                                   for i,(a,b) in enumerate(timings)]}
    (root/"demo.json").write_text(json.dumps(edl)+"\n")


if __name__=="__main__":
    make(Path.cwd())
