"""AV1 in IVF: IVF frames, OBUs (AV1 bitstream spec 5.3) and the sequence header (5.5)."""
import struct
from fractions import Fraction

OBU_NAMES = {1: "sequence_header", 2: "temporal_delimiter", 3: "frame_header", 4: "tile_group", 5: "metadata",
             6: "frame", 7: "redundant_frame_header", 8: "tile_list", 15: "padding"}


class Av1Error(ValueError):
    pass


def leb128(data, pos):
    return data[pos] & 0x7F, pos + 1


class Bits:
    def __init__(self, data):
        self.d, self.p = data, 0

    def f(self, n):
        v = 0
        for _ in range(n):
            byte = self.d[self.p >> 3]
            v = (v << 1) | ((byte >> (7 - (self.p & 7))) & 1)
            self.p += 1
        return v

    def uvlc(self):
        zeros = 0
        while not self.f(1):
            zeros += 1
            if zeros >= 32:
                return (1 << 32) - 1
        return self.f(zeros) + (1 << zeros) - 1


def sequence_header(payload):
    b = Bits(payload)
    out = {"profile": b.f(3), "still_picture": bool(b.f(1)), "reduced_still_picture_header": bool(b.f(1)),
           "timing": None, "operating_points": 1, "level": None, "tier": 0}
    if out["reduced_still_picture_header"]:
        out["level"] = b.f(5)
    else:
        decoder_model = False
        buffer_delay_len = 0
        b.f(1)                                       # timing_info_present_flag
        initial_delay = bool(b.f(1))
        count = b.f(5) + 1
        out["operating_points"] = count
        for i in range(count):
            b.f(12)                                  # operating_point_idc
            level = b.f(5)
            tier = b.f(1)
            if i == 0:
                out["level"], out["tier"] = level, tier
            if decoder_model and b.f(1):
                b.f(buffer_delay_len)
                b.f(buffer_delay_len)
                b.f(1)
            if initial_delay and b.f(1):
                b.f(4)
    wbits, hbits = b.f(4) + 1, b.f(4) + 1
    out["max_width"], out["max_height"] = b.f(wbits) + 1, b.f(hbits) + 1
    return out


def obus(data, start, end):
    pos, out = start, []
    while pos < end:
        h = data[pos]
        if h & 0x80:
            raise Av1Error("forbidden bit set in OBU header")
        otype, ext, has_size = (h >> 3) & 0xF, (h >> 2) & 1, (h >> 1) & 1
        p = pos + 1
        temporal = spatial = 0
        if ext:
            temporal, spatial = data[pos + 1] >> 5, (data[pos + 1] >> 3) & 3
        if has_size:
            size, p = leb128(data, p)
        else:
            size = end - p
        if p + size > end:
            raise Av1Error("OBU runs past the end of its frame")
        out.append({"type": otype, "payload": data[p:p + size], "temporal": temporal, "spatial": spatial})
        pos = p + size
    return out


def scan(data):
    if data[:4] != b"DKIF" or len(data) < 32:
        raise Av1Error("not an IVF file")
    version, hlen, fourcc, width, height, den, num, nframes = struct.unpack_from("<HH4sHHIII", data, 4)
    pos, frames, counts, seq, tus, pts = hlen, 0, {}, None, 0, []
    layers = set()
    while pos + 12 <= len(data):
        size, ts = struct.unpack_from("<IQ", data, pos)
        body = pos + 12
        if body + size > len(data):
            break
        for o in obus(data, body, body + size):
            name = OBU_NAMES.get(o["type"], "reserved")
            counts[name] = counts.get(name, 0) + 1
            layers.add((o["temporal"], o["spatial"]))
            if o["type"] == 1 and seq is None:
                seq = sequence_header(o["payload"])
            if o["type"] == 2:
                tus += 1
        frames += 1
        pts.append(ts)
        pos = body + size
    span = (max(pts) - min(pts) + (pts[1] - pts[0] if len(pts) > 1 else 1)) if pts else 0
    return {"fourcc": fourcc.decode("latin-1"), "width": width, "height": height,
            "timebase": "%d/%d" % (num, den), "ivf_frames": frames, "header_frame_count": nframes,
            "duration": str(Fraction(span * den, num)) if pts else "0",
            "obus": dict(sorted(counts.items())), "temporal_units": tus,
            "layers": len(layers), "sequence": seq}
