import subprocess
params = open("params_v0.txt").read().strip()
print(repr(params[-20:]))
r = subprocess.run(["/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg","-hide_banner","-nostdin","-y","-loglevel","error",
  "-f","rawvideo","-pixel_format","yuv420p","-video_size","64x48","-framerate","20","-i","video0.run.yuv",
  "-c:v","libx264","-preset","veryfast","-threads","1","-x264-params",params,
  "-video_track_timescale","1000","-an","params_test.mp4"],capture_output=True,text=True,timeout=120)
print("rc",r.returncode,r.stderr[-300:])
