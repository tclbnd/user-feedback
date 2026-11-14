import subprocess
params = open("params_v0.txt").read().strip().split()
print(len(params))
bad = []
for p in params:
    r = subprocess.run(["/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg","-hide_banner","-nostdin","-y","-loglevel","error",
      "-f","rawvideo","-pixel_format","yuv420p","-video_size","64x48","-framerate","20","-i","video0.run.yuv",
      "-c:v","libx264","-preset","veryfast","-threads","1","-x264-params",p,
      "-f","null","-"],capture_output=True,text=True,timeout=60)
    if r.returncode != 0:
        bad.append((p, r.stderr.strip().splitlines()[-1] if r.stderr.strip() else ""))
print("bad", bad)
