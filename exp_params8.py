import subprocess
FF="/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
base=["-f","rawvideo","-pixel_format","yuv420p","-video_size","64x48","-framerate","20","-i","video0.run.yuv",
      "-c:v","libx264","-preset","veryfast","-threads","1","-an"]
tests=["deblock=1,0","deblock=1:0","analyse=0x3,0x113","psy_rd=1.00,0.00","aq=1,1.00","cqm=0","deblock=1\\:0"]
for t in tests:
    r=subprocess.run([FF,"-hide_banner","-nostdin","-y","-loglevel","error"]+base+["-x264-params",t,"-f","null","-"],
                     capture_output=True,text=True,timeout=60)
    print(t, r.returncode, (r.stderr.strip().splitlines() or [""])[-1])
