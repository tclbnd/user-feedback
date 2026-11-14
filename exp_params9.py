import subprocess
from x264conv import convert
FF="/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
text = convert(open("params_v0.txt").read().strip())
open("converted.txt","w").write(text)
r=subprocess.run([FF,"-hide_banner","-nostdin","-y","-loglevel","error",
  "-f","rawvideo","-pixel_format","yuv420p","-video_size","64x48","-framerate","20","-i","video0.run.yuv",
  "-c:v","libx264","-preset","veryfast","-threads","1","-x264-params",text,
  "-video_track_timescale","1000","-an","params_test.mp4"],capture_output=True,text=True,timeout=120)
print("rc",r.returncode,r.stderr[-300:])
