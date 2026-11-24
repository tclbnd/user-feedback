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
    # FreeDB / CDDB: every track, addresses with the lead-in, sums of the digits of the start seconds
    n = sum(digit_sum(lba // 75) for _, _, lba in tracks)
    t = leadout // 75 - tracks[0][2] // 75
    cddb = "%08x" % ((n % 255) << 24 | t << 8 | len(tracks))
    # audio session: a trailing data track belongs to a second session
    audio = list(tracks)
    audio_leadout = leadout
    if not audio:
        raise DiscError("no audio tracks")
    first, last = audio[0][0], audio[-1][0]
    offsets = {num: lba + LEAD_IN for num, _, lba in audio}
    raw = "%02x%02x%08x" % (first, last, audio_leadout + LEAD_IN) + "".join("%08x" % offsets.get(i, 0) for i in range(1, 100))
    mb = base64.b64encode(hashlib.sha1(raw.encode()).digest()).decode()
    id1 = sum(lba for _, _, lba in audio) + audio_leadout
    id2 = sum(lba * num for num, _, lba in audio) + audio_leadout * (last + 1)
    ar = "%03d-%08x-%08x-%s" % (len(audio), id1 & 0xFFFFFFFF, id2 & 0xFFFFFFFF, cddb)
    return {"tracks": len(tracks), "audio_tracks": len(audio), "data_tracks": len(tracks) - len(audio),
            "audio_seconds": str(Fraction(audio_leadout - audio[0][2], 75)), "cddb_id": cddb,
            "musicbrainz_id": mb, "accuraterip_id": ar}
