import sys, subprocess
sys.path.insert(0, "scratch")

FF = "tools/ffmpeg"

def run(args):
    subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-nostdin", "-y"] + args, check=True)

if __name__ == "__main__":
    # encode 24 frames (2 GOPs) to see SPS/PPS + whether GOP boundaries align
    run(["-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "64x48",
         "-framerate", "20", "-i", "scratch/v0.yuv", "-frames:v", "24",
         "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
         "-x264-params", "keyint=12:min-keyint=12:scenecut=0:open-gop=0:bframes=2",
         "-video_track_timescale", "1000", "scratch/e24.mp4"])
    print("ok")
