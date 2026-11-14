import subprocess
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
# extract a run of 15 display-order frames from v0p.yuv
d = open("v0p.yuv", "rb").read()
fs = 64 * 48 * 3 // 2
run = d[fs * 3:fs * 18]
open("run.yuv", "wb").write(run)
args = [FF, "-hide_banner", "-loglevel", "error", "-y",
        "-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "64x48",
        "-framerate", "20", "-i", "run.yuv",
        "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
        "-x264-params", "keyint=12:min-keyint=12:scenecut=0:open-gop=0:bframes=2",
        "-video_track_timescale", "1000", "-an", "reenc.mp4"]
r = subprocess.run(args, capture_output=True, text=True)
print("rc", r.returncode, r.stderr[-500:])
