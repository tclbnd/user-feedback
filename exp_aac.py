import struct, subprocess, os
FF = "/private/tmp/zq-formal-d9qjt7dn/M01/work/tools/ffmpeg"
# click at sample 0, N=5000
N = 5000
pcm = bytearray(2 * N)
struct.pack_into("<hhh", pcm, 0, 30000, -30000, 30000)
open("click.pcm", "wb").write(pcm)
r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-f", "s16le", "-ar", "48000", "-ac", "1", "-i", "click.pcm", "-c:a", "aac", "-b:a", "160k", "-f", "adts", "click.aac"], capture_output=True, text=True)
print("enc", r.returncode, r.stderr[:300], os.path.getsize("click.aac"))
r = subprocess.run([FF, "-hide_banner", "-loglevel", "error", "-y", "-i", "click.aac", "-f", "s16le", "clickdec.pcm"], capture_output=True, text=True)
d = open("clickdec.pcm", "rb").read()
n = len(d) // 2
s = struct.unpack("<%dh" % n, d)
print("decoded samples", n, "frames", n / 1024)
peak = max(range(n), key=lambda i: abs(s[i]))
print("peak at", peak, s[peak])
# adts frame count
b = open("click.aac", "rb").read()
pos = 0
cnt = 0
sizes = []
while pos + 7 <= len(b):
    assert b[pos] == 0xFF and (b[pos + 1] & 0xF0) == 0xF0
    fl = ((b[pos + 3] & 0x03) << 11) | (b[pos + 4] << 3) | (b[pos + 5] >> 5)
    sizes.append(fl)
    pos += fl
    cnt += 1
print("adts frames", cnt, "sizes", sizes)
