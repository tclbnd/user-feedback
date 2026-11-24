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


def _musicbrainz_base64(digest):
    # MusicBrainz disc IDs are base64 with a URL-safe alphabet: '+' becomes
    # '.', '/' becomes '_' and the '=' padding becomes '-'.
    return (base64.b64encode(digest)
            .decode("ascii")
            .replace("+", ".")
            .replace("/", "_")
            .replace("=", "-"))


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

    # FreeDB / CDDB: every track counts, including a trailing data track.
    # Input LBAs omit the 150-sector lead-in, which the CDDB algorithm adds
    # before turning the start offsets into seconds.
    n = sum(digit_sum((lba + LEAD_IN) // 75) for _, _, lba in tracks)
    t = (leadout - tracks[0][2]) // 75
    cddb = "%08x" % (((n % 255) << 24) | (t << 8) | len(tracks))

    # Audio session: an Enhanced CD has the audio tracks followed by one or
    # more data tracks in a second session. MusicBrainz and AccurateRip only
    # describe the audio session; the session gap sits between its lead-out
    # and the first data track.
    split = len(tracks)
    for i, (_, kind, _) in enumerate(tracks):
        if kind != "AUDIO":
            split = i
            break
    audio = tracks[:split]
    if not audio:
        raise DiscError("no audio tracks")
    if split < len(tracks):
        audio_leadout = tracks[split][2] - ENHANCED_GAP
    else:
        audio_leadout = leadout

    first, last = audio[0][0], audio[-1][0]

    # MusicBrainz: first/last track number, the lead-out and then 99 track
    # offsets, all absolute sectors (input LBAs plus the 150-sector lead-in),
    # hashed as lowercase hex and encoded with the MusicBrainz base64 alphabet.
    offsets = {num: lba + LEAD_IN for num, _, lba in audio}
    raw = ("%02x%02x%08x" % (first, last, audio_leadout + LEAD_IN)
           + "".join("%08x" % offsets.get(i, 0) for i in range(1, 100)))
    mb = _musicbrainz_base64(hashlib.sha1(raw.encode("ascii")).digest())

    # AccurateRip: the two disc IDs sum absolute track offsets (LBA plus the
    # lead-in); the second ID also weights every offset by its track number
    # and the lead-out by the number of audio tracks plus one. With the
    # lead-in omitted, a first track at sector 0 would otherwise contribute
    # nothing to the second ID.
    id1 = sum(lba + LEAD_IN for _, _, lba in audio) + audio_leadout + LEAD_IN
    id2 = (sum((lba + LEAD_IN) * num for num, _, lba in audio)
           + (audio_leadout + LEAD_IN) * (len(audio) + 1))
    ar = "%03d-%08x-%08x-%s" % (len(audio),
                                id1 & 0xFFFFFFFF, id2 & 0xFFFFFFFF, cddb)

    return {"tracks": len(tracks), "audio_tracks": len(audio),
            "data_tracks": len(tracks) - len(audio),
            "audio_seconds": str(Fraction(audio_leadout - audio[0][2], 75)),
            "cddb_id": cddb, "musicbrainz_id": mb, "accuraterip_id": ar}
