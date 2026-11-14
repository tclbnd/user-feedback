import subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
for opts in [[], ["-advanced_editlist","1"], ["-advanced_editlist","0"]]:
    out = "edittest_%d.pcm" % len(opts)
    args=[FF,"-hide_banner","-loglevel","error","-y"]+opts+["-i","video0.mp4","-vn","-f","s16le",out]
    r=subprocess.run(args,capture_output=True,text=True)
    print(opts, r.returncode, os.path.getsize(out)//2 if os.path.exists(out) else -1, r.stderr[:200])
