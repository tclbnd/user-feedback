import subprocess, re, os, sys
from inspect_mp4 import track_infos, samples_from_info

FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"

def nal_units(payload):
    out = []
    pos = 0
    while pos + 4 <= len(payload):
        n = int.from_bytes(payload[pos:pos+4], "big")
        pos += 4
        if n <= 0 or pos + n > len(payload):
            break
        out.append(payload[pos:pos+n])
        pos += n
    return out

def x264_params(path):
    data, infos = track_infos(path)
    v = [i for i in infos if i.get("handler") == "vide"][0]
    s = samples_from_info(data, v)[0]
    payload = data[s["offset"]:s["offset"]+s["size"]]
    for nal in nal_units(payload):
        if nal[0] & 31 == 6:
            idx = nal.find(b"options: ")
            if idx >= 0:
                return nal[idx+9:].decode("latin1", "replace")
    return None

for src in ["video0.mp4", "video1.mp4", "video2.mp4"]:
    print("==", src, x264_params(src))

def probe_avcc(path):
    data, infos = track_infos(path)
    v = [i for i in infos if i.get("handler") == "vide"][0]
    return v["avcC"].hex()

def enc(region_yuv, params, out):
    args = [FF, "-hide_banner", "-nostdin", "-y", "-loglevel", "error",
            "-f", "rawvideo", "-pixel_format", "yuv420p", "-video_size", "64x48",
            "-framerate", "20", "-i", region_yuv,
            "-c:v", "libx264", "-preset", "veryfast", "-threads", "1",
            "-x264-params", params, "-video_track_timescale", "1000", "-an", out]
    r = subprocess.run(args, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stderr.strip()

# take frames 0..14 from video2 decoded yuv
subprocess.run([FF, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-i", "video2.mp4",
                "-map", "0:v", "-fps_mode", "passthrough", "-f", "rawvideo", "-pix_fmt", "yuv420p", "v2p.yuv"], check=True, timeout=120)
d = open("v2p.yuv", "rb").read()
fs = 64*48*3//2
open("v2seg.yuv", "wb").write(d[fs*2:fs*17])
params = x264_params("video2.mp4")
rc, err = enc("v2seg.yuv", params, "v2enc.mp4")
print("enc rc", rc, err[-400:])
print("src avcC", probe_avcc("video2.mp4"))
print("enc avcC", probe_avcc("v2enc.mp4"))
