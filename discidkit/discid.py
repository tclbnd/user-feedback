"""Disc identifiers from a CD table of contents: FreeDB/CDDB, MusicBrainz and AccurateRip."""
import base64
import hashlib
from fractions import Fraction

LEAD_IN = 150                     # the 2-second pregap every TOC address omits
ENHANCED_GAP = 11400              # sectors between the audio session and a data session (Enhanced CD)


class DiscError(ValueError):
    pass


def digit_sum(n):
    return sum(int(c) for c in str(n))


def parse(text):
    tracks, leadout = [], None
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        f = line.split()
        if f[0].upper() == "TRACK" and len(f) == 4:
            tracks.append((int(f[1]), f[2].upper(), int(f[3])))
        elif f[0].upper() == "LEADOUT" and len(f) == 2:
            leadout = int(f[1])
        else:
            raise DiscError("bad TOC line %r" % line)
    if not tracks or leadout is None:
        raise DiscError("TOC needs tracks and a lead-out")
    tracks.sort()
    numbers = [n for n, _, _ in tracks]
    if numbers != list(range(numbers[0], numbers[0] + len(numbers))):
        raise DiscError("track numbers are not consecutive")

    # FreeDB / CDDB: counts every track (data tracks included). The lead-in is
    # part of every address, so each start second is lba/75 plus the 2-second
    # lead-in before the digit sum is taken.
    n = sum(digit_sum(lba // 75 + LEAD_IN // 75) for _, _, lba in tracks)
    t = leadout // 75 - tracks[0][2] // 75
    cddb = "%08x" % ((n % 255) << 24 | t << 8 | len(tracks))

    # Enhanced CD: a trailing data track belongs to a second session. The
    # MusicBrainz and AccurateRip IDs describe the audio session only; its
    # lead-out is the data track start minus the inter-session gap.
    audio = [(num, kind, lba) for num, kind, lba in tracks if kind == "AUDIO"]
    if not audio:
        raise DiscError("no audio tracks")
    audio_leadout = leadout
    if len(audio) < len(tracks):
        data_start = tracks[len(audio)][2]
        audio_leadout = data_start - ENHANCED_GAP

    first, last = audio[0][0], audio[-1][0]
    offsets = {num: lba + LEAD_IN for num, _, lba in audio}
    leadout_offset = audio_leadout + LEAD_IN

    # MusicBrainz: upper-case hex, one fixed zero word followed by 99 offset
    # words, then the URL-safe base64 of the SHA-1 digest (no padding,
    # '+' -> '.', '/' -> '_').
    raw = ("%02X%02X%08X" % (first, last, leadout_offset)
           + "".join("%08X" % offsets.get(i, 0) for i in range(1, 100)))
    mb = (base64.b64encode(hashlib.sha1(raw.encode("ascii")).digest())
          .decode("ascii").rstrip("=").replace("+", ".").replace("/", "_"))

    # AccurateRip: every address includes the lead-in. For a disc whose first
    # track starts at LBA 0 (no lead-in reported), the second ID omits the
    # lead-out term, matching the reference implementation's special case.
    id1 = sum(offsets[num] for num, _, _ in audio) + leadout_offset
    id2 = sum(offsets[num] * num for num, _, _ in audio)
    if audio[0][2] != 0:
        id2 += leadout_offset * (last + 1)
    ar = "%03d-%08x-%08x-%s" % (len(audio), id1 & 0xFFFFFFFF, id2 & 0xFFFFFFFF, cddb)

    return {"tracks": len(tracks), "audio_tracks": len(audio), "data_tracks": len(tracks) - len(audio),
            "audio_seconds": str(Fraction(audio_leadout - audio[0][2], 75)), "cddb_id": cddb,
            "musicbrainz_id": mb, "accuraterip_id": ar}
