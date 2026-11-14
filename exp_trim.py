import subprocess, os, json
from inspect_mp4 import track_infos, samples_from_info

FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
args = [FF, "-hide_banner", "-loglevel", "error", "-y", "-i", "video0.mp4",
        "-vf", "trim=start=0.7:end=1.85", "-fps_mode", "passthrough",
        "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
        "-x264-params", "keyint=12:min-keyint=12:scenecut=0:open-gop=0:bframes=2",
        "-video_track_timescale", "1000", "-an", "trimtest.mp4"]
r = subprocess.run(args, capture_output=True, text=True)
print(r.returncode, r.stderr[:2000])
data, infos = track_infos("trimtest.mp4")
v = [i for i in infos if i.get("handler") == "vide"][0]
for s in samples_from_info(data, v):
    print(s["i"], "key" if s["key"] else "   ", "dts", s["dts"], "pts", s["pts"], "cto", s["pts"] - s["dts"], "size", s["size"])
