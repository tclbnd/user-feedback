"""Sega Genesis / Mega Drive register logs (.gym): GYMX header, zlib packing, YM2612 and PSG writes at 60 Hz.

Gens writes one frame every 1/60 s. The stream commands are:

* 0x00                 wait one frame
* 0x01 reg val         YM2612 port 0 register write
* 0x02 reg val         YM2612 port 1 register write
* 0x03 val             SN76489 PSG write (a single byte)

A GYMX file prepends a 428-byte header (magic, five text fields, a 256-byte
comment, the 1-based loop frame and the unpacked stream size) and stores the
stream zlib-compressed.
"""
import struct
import zlib
from fractions import Fraction


FRAME_RATE = 60
GYMX_SIZE = 4 + 5 * 32 + 256 + 2 * 4
SN76489_MUTED = 15


class GymError(ValueError):
    pass


def _text(raw):
    return raw.split(b"\0")[0].decode("latin-1").strip() or None


def _parse_header(data):
    """Split off the GYMX header; return (header dict or None, packed stream)."""
    if data[:4] != b"GYMX":
        return None, data
    if len(data) < GYMX_SIZE:
        raise GymError("truncated GYMX header: %d of %d bytes" % (len(data), GYMX_SIZE))
    song, game, publisher, emulator, dumper, comment, loop_frame, packed_size = \
        struct.unpack_from("<32s32s32s32s32s256sII", data, 4)
    body = data[GYMX_SIZE:]
    packed = body[:2] in (b"\x78\x01", b"\x78\x5E", b"\x78\x9C", b"\x78\xDA")
    if packed:
        try:
            body = zlib.decompress(body)
        except zlib.error as exc:
            raise GymError("bad zlib stream: %s" % exc)
        if packed_size and len(body) != packed_size:
            raise GymError("unpacked size %d does not match header value %d"
                           % (len(body), packed_size))
    header = {
        "song": _text(song),
        "game": _text(game),
        "publisher": _text(publisher),
        "emulator": _text(emulator),
        "dumper": _text(dumper),
        "comment": _text(comment),
        "loop_frame": loop_frame or None,
        "packed": packed,
    }
    return header, body


def parse(data):
    header, body = _parse_header(data)

    frames = 0
    port0 = port1 = psg = dac = 0
    dac_enabled = False
    # SN76489 channels 0-3 (channel 3 is noise), attenuation starts muted.
    psg_volume_writes = [0, 0, 0, 0]
    psg_volumes = [SN76489_MUTED] * 4
    # FM key-on counts for channels 1-6 (index 0-5); invalid codes are ignored.
    key_ons = [0, 0, 0, 0, 0, 0]

    p, n = 0, len(body)
    while p < n:
        op = body[p]
        if op == 0x00:
            frames += 1
            p += 1
        elif op in (0x01, 0x02):
            if p + 3 > n:
                raise GymError("truncated YM2612 write at %d" % p)
            reg, val = body[p + 1], body[p + 2]
            if op == 0x01:
                port0 += 1
                if reg == 0x2A:
                    # DAC sample data register.
                    dac += 1
                elif reg == 0x27:
                    # DAC enable/disable: bit 7 selects PCM mode.
                    dac_enabled = bool(val & 0x80)
                elif reg == 0x28 and val & 0xF0:
                    # Key-on register: low 3 bits select the channel (0-5);
                    # 6 and 7 are unused/invalid channel codes.
                    ch = val & 0x07
                    if ch < 6:
                        key_ons[ch] += 1
            else:
                port1 += 1
            p += 3
        elif op == 0x03:
            if p + 2 > n:
                raise GymError("truncated PSG write at %d" % p)
            val = body[p + 1]
            psg += 1
            # Latch byte (bit 7 set). The register number is bits 6:4: bit 4
            # (R0) selects volume/attenuation when set, tone/noise otherwise;
            # bits 6:5 (R2:R1) select the channel (3 = noise).
            if (val & 0x90) == 0x90:
                ch = (val >> 5) & 0x03
                psg_volume_writes[ch] += 1
                psg_volumes[ch] = val & 0x0F
            p += 2
        else:
            raise GymError("unknown command 0x%02X at %d" % (op, p))

    out = {
        "header": header,
        "frames": frames,
        "duration": str(Fraction(frames, FRAME_RATE)),
        "ym_port0_writes": port0,
        "ym_port1_writes": port1,
        "psg_writes": psg,
        "dac_writes": dac,
        "dac_enabled": dac_enabled,
        "psg_volume_writes": {str(ch): psg_volume_writes[ch] for ch in range(4)},
        "psg_volumes": psg_volumes,
        "key_ons": {str(ch + 1): key_ons[ch] for ch in range(6) if key_ons[ch]},
        "loop_seconds": None,
    }
    if header is not None and header["loop_frame"]:
        # Stored frames are 1-based; frame 1 is the start of the song.
        out["loop_seconds"] = str(Fraction(header["loop_frame"] - 1, FRAME_RATE))
    return out
