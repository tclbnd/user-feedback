"""A minimal DLS bank: one instrument, one region, one wave."""
import struct


def ck(cid, body):
    out = cid + struct.pack("<I", len(body)) + body
    if len(body) & 1:                      # RIFF pad byte after odd-sized chunks
        out += b"\0"
    return out


def bank(frames=100):
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    wave = ck(b"LIST", b"wave" + ck(b"fmt ", fmt) + ck(b"data", bytes(frames * 2)))
    rgn = ck(b"LIST", b"rgn " + ck(b"rgnh", struct.pack("<6H", 0, 127, 0, 127, 0, 0)) + ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, 0)))
    ins = ck(b"LIST", b"ins " + ck(b"insh", struct.pack("<III", 1, 0, 5)) + ck(b"LIST", b"lrgn" + rgn))
    body = b"DLS " + ck(b"colh", struct.pack("<I", 1)) + ck(b"LIST", b"lins" + ins)
    body += ck(b"ptbl", struct.pack("<III", 8, 1, 0)) + ck(b"LIST", b"wvpl" + wave)
    return b"RIFF" + struct.pack("<I", len(body)) + body


def wsmp(unity=60, gain=0, loop=None):
    body = struct.pack("<IHhiII", 20, unity, 0, gain, 0, 1 if loop else 0)
    if loop:
        body += struct.pack("<IIIII", 16, loop[0], loop[1], loop[2], 0)
    return ck(b"wsmp", body)


def rich_bank():
    """Two instruments and two waves exercising every corrected path.

    * drum instrument with a shuffled pool table and odd-length names,
    * DLS2 rgn2 regions, one without a region wsmp (falls back to the wave's),
    * an odd-length wave data block (RIFF padding) followed by another wave,
    * banks outside bank 0 (MSB/LSB split, drum flag).
    """
    fmt = struct.pack("<HHIIHH", 1, 1, 22050, 44100, 2, 16)
    wave0 = ck(b"LIST", b"wave" + ck(b"fmt ", fmt) + wsmp(48, 32768)
               + ck(b"data", b"abc"))           # 3 data bytes: odd chunk, padded
    wave1 = ck(b"LIST", b"wave" + ck(b"fmt ", fmt) + ck(b"data", bytes(10 * 2)))
    pool = wave0 + wave1
    cues = [len(wave0), 0]                      # table index 0 -> wave 1, 1 -> wave 0
    ptbl = ck(b"ptbl", struct.pack("<II", 8, len(cues)) + struct.pack("<%dI" % len(cues), *cues))

    def region(kind, lo, hi, table_index, with_wsmp):
        body = ck(b"rgnh", struct.pack("<6H", lo, hi, 0, 127, 0, 0))
        if with_wsmp:
            body += wsmp(60)
        body += ck(b"wlnk", struct.pack("<HHII", 0, 0, 1, table_index))
        return ck(b"LIST", kind + body)

    drums = region(b"rgn2", 35, 36, 1, True) + region(b"rgn2", 38, 40, 0, False)
    # bank 0x80007801: drum flag, MSB 120, LSB 1
    drum_ins = (ck(b"insh", struct.pack("<III", 2, 0x80007801, 0))
                + ck(b"LIST", b"lrgn" + drums)
                + ck(b"LIST", b"INFO" + ck(b"INAM", b"Kit\0")))   # odd INAM
    melodic = region(b"rgn ", 0, 127, 1, False)                    # no region wsmp
    # bank 0x00000805: MSB 8, LSB 5
    mel_ins = (ck(b"insh", struct.pack("<III", 1, 0x00000805, 33))
               + ck(b"LIST", b"lrgn" + melodic)
               + ck(b"LIST", b"INFO" + ck(b"INAM", b"Strings\0")))  # odd INAM
    lins = ck(b"LIST", b"lins" + ck(b"LIST", b"ins " + drum_ins) + ck(b"LIST", b"ins " + mel_ins))
    body = (b"DLS " + ck(b"colh", struct.pack("<I", 2)) + lins
            + ptbl + ck(b"LIST", b"wvpl" + pool))
    return b"RIFF" + struct.pack("<I", len(body)) + body

