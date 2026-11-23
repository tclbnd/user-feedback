"""DVD-Video title set information (VTS_xx_0.IFO): attributes, program chains, cells, chapters.

All multi-byte fields are big endian.  Offsets follow the DVD-Video VTS IFO
layout (ECMA-267 / DVD-Video book):

* 0xC8: PTT (part-of-title / chapter) table start sector
* 0xCC: PGCI (program chain information) table start sector
* 0x200: video stream attributes (VTS_VST_ATR), a 16-bit word
* 0x202: number of audio streams; each audio attribute is 8 bytes from 0x204
* 0x254: number of subpicture streams; each attribute is 6 bytes from 0x256

Sectors are 2048 bytes.
"""
import struct
from fractions import Fraction

SECTOR = 2048

# Audio coding mode: bits 7..5 of audio attribute byte 0.
CODECS = {0: "AC-3", 2: "MPEG-1", 3: "MPEG-2", 4: "LPCM", 6: "DTS"}

# Audio language type (code extension): bits 7..5 of audio attribute byte 1.
PURPOSES = {0: None, 1: "normal", 2: "visually_impaired",
            3: "directors_comments", 4: "alternate_directors_comments"}

# LPCM quantisation / dynamic range control: bits 4..3 of audio attribute byte 0.
LPCM_BITS = {0: 16, 1: 20, 2: 24}  # 3 = dynamic range control -> null

# LPCM sample rate: bits 2..0 of audio attribute byte 0.
LPCM_RATES = {0: 48000, 1: 96000, 2: 44100, 3: 32000}


class IfoError(ValueError):
    pass


def bcd(b):
    return (b >> 4) * 10 + (b & 0x0F)


def ptime(raw):
    """DVD playback time: BCD hh mm ss ff.

    The top two bits of the frame byte give the frame rate of the time
    stamp itself (01 = 25 fps PAL, 11 = 29.97/30 fps NTSC), so every time
    stamp is converted with its own rate rather than a fixed one.
    """
    hh, mm, ss, ff = raw
    fps = 25 if (ff & 0xC0) == 0x40 else 30
    frames = bcd(ff & 0x3F)
    return (Fraction(bcd(hh) * 3600 + bcd(mm) * 60 + bcd(ss))
            + Fraction(frames, fps))


def lang(b):
    code = b.decode("latin-1")
    return code if code.isalpha() else None


def audio_attr(raw):
    """Parse one 8-byte VTS audio stream attribute (VTS_VTS_ATR#n).

    Byte 0: bits 7..5 coding mode, 4..3 application mode / LPCM
    quantisation, 2..0 reserved (LPCM: sample rate).
    Byte 1: bits 7..5 language type, 4..3 reserved, 2..0 channels - 1.
    Bytes 2..3: language code.
    """
    b0, b1 = raw[0], raw[1]
    codec_id = b0 >> 5
    codec = CODECS.get(codec_id, "type_%d" % codec_id)
    channels = (b1 & 0x07) + 1  # field stores number of channels minus one
    mode = (b0 >> 3) & 0x03

    if codec_id == 4:  # LPCM
        sample_rate = LPCM_RATES.get(b0 & 0x07, 48000)
        bits = LPCM_BITS.get(mode)  # None for dynamic range control
    else:
        sample_rate = 48000
        bits = None

    if codec_id in (2, 3):  # MPEG audio application mode
        application = {1: "karaoke", 2: "surround"}.get(mode)
    else:
        application = None

    return {"codec": codec, "channels": channels, "sample_rate": sample_rate,
            "language": lang(raw[2:4]), "bits": bits,
            "application": application, "purpose": PURPOSES.get(b1 >> 5)}


def pgc(data, at):
    programs, cells = data[at + 2], data[at + 3]
    total = ptime(data[at + 4:at + 8])
    pmap, cpb = struct.unpack_from(">HH", data, at + 0xE6)

    # Program map: entry #p is the 1-based cell number where program p starts.
    entries = [data[at + pmap + i] for i in range(programs)] if programs else []

    # Cell playback information (C_PBIT), 24 bytes per cell.  Byte 0 carries
    # cell type (bits 7..6) and cell block mode (bits 5..4); the playback
    # time is bytes 4..7 and the still time (in seconds, 255 = infinite) is
    # byte 22.
    cell_times = []
    block_modes = []
    still_cells = []
    for i in range(cells):
        c = at + cpb + 24 * i
        block_modes.append((data[c] >> 4) & 3)
        cell_times.append(ptime(data[c + 4:c + 8]))
        if data[c + 22]:
            still_cells.append(i + 1)

    # Cumulative playback time.  An interleaved/angle block marks its first
    # cell with block mode 1 and the remaining cells with 2 or 3; those
    # alternative presentations are the same material per angle, so the
    # block is counted once and only the first cell's time is added.
    cell_starts = []
    running = Fraction(0)
    for t, mode in zip(cell_times, block_modes):
        cell_starts.append(running)
        if mode in (0, 1):
            running += t
    cell_total = running

    # A chapter starts at its program's entry cell; index by the entry cell
    # number from the program map, not by the program ordinal.
    starts = [str(cell_starts[e - 1]) for e in entries]

    return {"programs": programs, "cells": cells, "duration": str(total),
            "cell_total": str(cell_total), "program_starts": starts,
            "still_cells": still_cells}


def parse(data):
    if data[:12] != b"DVDVIDEO-VTS":
        raise IfoError("not a DVD video title set IFO")
    ptt_sector, pgci_sector = struct.unpack_from(">II", data, 0xC8)
    video = struct.unpack_from(">H", data, 0x200)[0]
    # VTS_VST_ATR word: bits 15..14 are the TV system (0 = NTSC, 1 = PAL,
    # 2..3 = reserved), bits 13..12 the display aspect ratio (0 = 4:3,
    # 1 = letterboxed 4:3, 2 = reserved, 3 = anamorphic 16:9).
    out = {"standard": "PAL" if (video >> 14) & 3 == 1 else "NTSC",
           "aspect": "16:9" if (video >> 12) & 3 == 3 else "4:3",
           "audio": [], "subpictures": []}
    for i in range(min(struct.unpack_from(">H", data, 0x202)[0], 8)):
        out["audio"].append(audio_attr(data[0x204 + 8 * i:0x20C + 8 * i]))
    for i in range(min(struct.unpack_from(">H", data, 0x254)[0], 32)):
        s = 0x256 + 6 * i
        out["subpictures"].append(lang(data[s + 2:s + 4]))
    base = pgci_sector * SECTOR
    if base + 8 > len(data):
        raise IfoError("program chain table outside the file")
    count = struct.unpack_from(">H", data, base)[0]
    out["pgcs"] = []
    for i in range(count):
        off = struct.unpack_from(">I", data, base + 8 + 8 * i + 4)[0]
        out["pgcs"].append(pgc(data, base + off))
    tbase = ptt_sector * SECTOR
    titles = []
    ntitles, _, end = struct.unpack_from(">HHI", data, tbase)
    offs = [struct.unpack_from(">I", data, tbase + 8 + 4 * i)[0] for i in range(ntitles)] + [end + 1]
    for i in range(ntitles):
        chapters = []
        for p in range(tbase + offs[i], tbase + offs[i + 1], 4):
            pgcn, pgn = struct.unpack_from(">HH", data, p)
            chapters.append(out["pgcs"][pgcn - 1]["program_starts"][pgn - 1])
        titles.append({"chapters": len(chapters), "chapter_starts": chapters})
    out["titles"] = titles
    return out
