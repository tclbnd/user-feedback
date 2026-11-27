"""Game DPCM audio streams (.dpk): Interplay, Xan and Sierra SOL DPCM packets decoded to PCM."""
import zlib

MAGIC = b"DPCMPAK\0"
INTERPLAY = (
    0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15,
    16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
    32, 33, 34, 35, 36, 37, 38, 39, 40, 41, 42, 43, 47, 51, 56, 61,
    66, 72, 79, 86, 94, 102, 112, 122, 133, 145, 158, 173, 189, 206, 225, 245,
    267, 292, 318, 348, 379, 414, 452, 493, 538, 587, 640, 699, 763, 832, 908, 991,
    1081, 1180, 1288, 1405, 1534, 1673, 1826, 1993, 2175, 2373, 2590, 2826, 3084, 3365, 3672, 4008,
    4373, 4772, 5208, 5683, 6202, 6767, 7385, 8059, 8794, 9597, 10472, 11428, 12471, 13609, 14851, 16206,
    17685, 19298, 21060, 22981, 25078, 27367, 29864, 32589, -29973, -26728, -23186, -19322, -15105, -10503, -5481, -1,
    1, 1, 5481, 10503, 15105, 19322, 23186, 26728, 29973, -32589, -29864, -27367, -25078, -22981, -21060, -19298,
    -17685, -16206, -14851, -13609, -12471, -11428, -10472, -9597, -8794, -8059, -7385, -6767, -6202, -5683, -5208, -4772,
    -4373, -4008, -3672, -3365, -3084, -2826, -2590, -2373, -2175, -1993, -1826, -1673, -1534, -1405, -1288, -1180,
    -1081, -991, -908, -832, -763, -699, -640, -587, -538, -493, -452, -414, -379, -348, -318, -292,
    -267, -245, -225, -206, -189, -173, -158, -145, -133, -122, -112, -102, -94, -86, -79, -72,
    -66, -61, -56, -51, -47, -43, -42, -41, -40, -39, -38, -37, -36, -35, -34, -33,
    -32, -31, -30, -29, -28, -27, -26, -25, -24, -23, -22, -21, -20, -19, -18, -17,
    -16, -15, -14, -13, -12, -11, -10, -9, -8, -7, -6, -5, -4, -3, -2, -1,
)
SOL_OLD = (
    0, 1, 2, 3, 6, 10, 15, 21, -21, -15, -10, -6, -3, -2, -1, 0,
)
SOL_NEW = (
    0, 1, 2, 3, 6, 10, 15, 21, 0, -1, -2, -3, -6, -10, -15, -21,
)
SOL_16 = (
    0, 8, 16, 32, 48, 64, 80, 96, 112, 128, 144, 160, 176, 192, 208, 224,
    240, 256, 272, 288, 304, 320, 336, 352, 368, 384, 400, 416, 432, 448, 464, 480,
    496, 512, 520, 528, 536, 544, 552, 560, 568, 576, 584, 592, 600, 608, 616, 624,
    632, 640, 648, 656, 664, 672, 680, 688, 696, 704, 712, 720, 728, 736, 744, 752,
    760, 768, 776, 784, 792, 800, 808, 816, 824, 832, 840, 848, 856, 864, 872, 880,
    888, 896, 904, 912, 920, 928, 936, 944, 952, 960, 968, 976, 984, 992, 1000, 1008,
    1016, 1024, 1088, 1152, 1216, 1280, 1344, 1408, 1472, 1536, 1600, 1664, 1728, 1792, 1856, 1920,
    1984, 2048, 2304, 2560, 2816, 3072, 3328, 3584, 3840, 4096, 5120, 6144, 7168, 8192, 12288, 16384,
)
CODECS = {b"IPLY": "interplay", b"XAN ": "xan", b"SOL1": "sol-old", b"SOL2": "sol-new", b"SOL3": "sol-16"}


class DpcmError(ValueError):
    pass


def _s16(v):
    v &= 0xFFFF
    return v - 0x10000 if v & 0x8000 else v


def _c16(v):
    return -32768 if v < -32768 else 32767 if v > 32767 else v


def _c8(v):
    return 0 if v < 0 else 255 if v > 255 else v


class Decoder:
    def __init__(self, codec, channels):
        self.codec, self.ch = codec, channels
        # SOL keeps its running predictor across packets; Xan keeps the
        # adaptive shift across packets. Interplay takes a fresh predictor
        # from the seed carried in every packet.
        self.sample = [0, 0]
        self.shift = [4, 4]

    def packet(self, buf):
        stereo = self.ch - 1
        n = len(buf)
        c = self.codec
        out = {
            # Seed samples (one per channel) plus every remaining delta
            # byte; in stereo an odd trailing byte only feeds channel 0.
            "interplay": self.ch + max(n - 6 - 2 * self.ch, 0),
            "xan": (n - 2 * self.ch) * self.ch,
            "sol-old": n * 2,
            "sol-new": n * 2,
            "sol-16": n,
        }[c]
        if out <= 0:
            raise DpcmError("packet is too small")
        res = []
        if c == "interplay":
            # Six-byte MVE chunk header, then a 16-bit predictor seed per
            # channel; the seeds themselves are the first output samples.
            p = 6
            pred = [0, 0]
            for k in range(self.ch):
                pred[k] = _s16(buf[p] | buf[p + 1] << 8)
                res.append(pred[k])
                p += 2
            ch = 0
            while len(res) < out and p < n:
                pred[ch] = _c16(pred[ch] + INTERPLAY[buf[p]])
                p += 1
                res.append(pred[ch])
                ch ^= stereo
        elif c == "xan":
            # A 16-bit predictor seed per channel (not emitted), then one
            # delta byte per sample. The two low bits steer the adaptive
            # shift, which persists across packets.
            p = 0
            pred = [0, 0]
            for k in range(self.ch):
                pred[k] = _s16(buf[p] | buf[p + 1] << 8)
                p += 2
            ch = 0
            while len(res) < out and p < n:
                d = buf[p]
                p += 1
                m = d & 3
                if m == 3:
                    self.shift[ch] -= 1
                else:
                    self.shift[ch] += 2 * m
                self.shift[ch] = max(0, min(10, self.shift[ch]))
                diff = _s16((d & ~3 & 0xFF) << 8) >> self.shift[ch]
                pred[ch] = _c16(pred[ch] + diff)
                res.append(pred[ch])
                ch ^= stereo
        elif c in ("sol-old", "sol-new"):
            # High nibble first (channel 0), then low nibble (channel 1);
            # the predictor carries over between packets.
            tab = SOL_OLD if c == "sol-old" else SOL_NEW
            for b in buf:
                self.sample[0] = _c8(self.sample[0] + tab[b >> 4])
                res.append(self.sample[0])
                self.sample[stereo] = _c8(self.sample[stereo] + tab[b & 0x0F])
                res.append(self.sample[stereo])
        else:
            # Top bit marks a negative step; the step is the 7-bit table
            # value centred around zero. Predictor wraps like int16.
            ch = 0
            for b in buf:
                step = SOL_16[b & 0x7F]
                if b >= 0x80:
                    step -= 0x4000
                self.sample[ch] = _s16(self.sample[ch] + step)
                res.append(self.sample[ch])
                ch ^= stereo
        return res[:out]


def parse(data):
    if len(data) < 18 or data[:8] != MAGIC:
        raise DpcmError("not a DPCM packet file")
    tag = data[8:12]
    if tag not in CODECS:
        raise DpcmError("unknown codec tag")
    codec = CODECS[tag]
    channels = data[12]
    if channels not in (1, 2):
        raise DpcmError("channels must be 1 or 2")
    rate = int.from_bytes(data[13:17], "little")
    count = data[17] | 0
    pos = 18
    eight = codec in ("sol-old", "sol-new")
    allv, packets = [], []
    decoder = Decoder(codec, channels)
    for _ in range(count):
        if pos + 4 > len(data):
            raise DpcmError("packet table is truncated")
        n = int.from_bytes(data[pos:pos + 4], "little")
        body = data[pos + 4:pos + 4 + n]
        if len(body) < n:
            raise DpcmError("packet data is truncated")
        pos += 4 + n
        try:
            s = decoder.packet(body)
            packets.append(len(s))
            allv += s
        except DpcmError:
            packets.append(0)
    chans = []
    for k in range(channels):
        v = allv[k::channels]
        if eight:
            raw = bytes(v)
            centred = [x - 128 for x in v]
        else:
            raw = b"".join(x.to_bytes(2, "little", signed=True) for x in v)
            centred = v
        chans.append({"samples": len(v), "peak": max((abs(x) for x in centred), default=0), "sum": sum(centred),
                      "crc32": "%08x" % zlib.crc32(raw)})
    frames = len(allv) // channels
    return {"codec": codec, "channels": channels, "bits": 8 if eight else 16, "rate": rate, "packets": packets,
            "frames": frames, "duration": round(frames / rate, 4) if rate else None, "channel": chans}
