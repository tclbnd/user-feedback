import subprocess, sys, os

FF = "tools/ffmpeg"

def run(args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=True)

def enc(out, frames=12, extra=None, src="scratch/v0.yuv"):
    args = ["-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "64x48",
            "-framerate", "20", "-i", src, "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
            "-x264-params", "keyint=12:min-keyint=12:scenecut=0:open-gop=0:bframes=2",
            "-video_track_timescale", "1000"]
    run(args + [out])

def enc_bf0(out, frames=12):
    args = ["-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "64x48",
            "-framerate", "20", "-i", "scratch/v0.yuv", "-frames:v", str(frames),
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
            "-x264-params", "keyint=12:min-keyint=12:scenecut=0:open-gop=0:bframes=0",
            "-video_track_timescale", "1000"]
    run(args + [out])

if __name__ == "__main__":
    enc("scratch/e_bf2.mp4", 12)
    enc_bf0("scratch/e_bf0.mp4", 12)
    print("done")
