import json
import random
import struct
import sys
import zlib

sys.path.insert(0, ".")
import unframe


def frame(seq, payload):
    body = struct.pack(">II", seq, len(payload)) + payload
    return struct.pack(">III", seq, len(payload), zlib.crc32(body)) + payload


rng = random.Random(1)
pa = bytes(rng.randrange(256) for _ in range(64))
pb = bytes(rng.randrange(256) for _ in range(64))
pc = bytes(rng.randrange(256) for _ in range(64))
fa, fb, fc = frame(0, pa), frame(1, pb), frame(2, pc)
off_a, off_b, off_c = 8, 8 + len(fa), 8 + len(fa) + len(fb)
s = bytearray(b"ZQAF0001" + fa + fb + fc)
fake_len = len(fb) - 4
print("fake_len", fake_len, "claimed end", 8 + 12 + fake_len,
      "off_b", off_b, "off_c", off_c)
struct.pack_into(">I", s, off_a + 4, fake_len)
print(json.dumps(unframe.parse_stream(bytes(s)[8:], base=8), indent=1))

body = bytes(s)[8:]
print("valid rel at B:", unframe._valid_frame_at(body, off_b - 8))
