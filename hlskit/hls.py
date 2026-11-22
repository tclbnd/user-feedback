"""HTTP Live Streaming playlists (RFC 8216): media and master playlists."""
import re
from decimal import Decimal
from fractions import Fraction


class HlsError(ValueError):
    pass


def attrs(text):
    out = {}
    for part in text.split(","):
        if "=" in part:
            k, v = part.split("=", 1)
            out[k.strip()] = v.strip().strip('"')
    return out


def urljoin(base, uri):
    if not base or "://" in uri:
        return uri
    return base.rsplit("/", 1)[0] + "/" + uri


def _range(text):
    if "@" in text:
        n, o = text.split("@", 1)
        return int(n), int(o)
    return int(text), None


def parse(text, base=""):
    lines = [l.strip() for l in text.replace("\r\n", "\n").split("\n")]
    lines = [l for l in lines if l]
    if not lines or lines[0] != "#EXTM3U":
        raise HlsError("not an M3U8 playlist")
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        return _master(lines, base)
    return _media(lines, base)


def _master(lines, base):
    variants, media = [], 0
    for i, l in enumerate(lines):
        if l.startswith("#EXT-X-STREAM-INF:"):
            a = attrs(l.split(":", 1)[1])
            uri = next((x for x in lines[i + 1:] if not x.startswith("#")), None)
            variants.append({"bandwidth": int(a["BANDWIDTH"]), "codecs": a.get("CODECS"),
                             "resolution": a.get("RESOLUTION"), "uri": urljoin(base, uri) if uri else None})
        elif l.startswith("#EXT-X-MEDIA:"):
            media += 1
    return {"type": "master", "variants": variants, "renditions": media}


def _media(lines, base):
    info = {"type": "media", "version": 1, "target_duration": None, "media_sequence": 0,
            "discontinuity_sequence": 0, "endlist": False, "playlist_type": None}
    segs = []
    pending = {"duration": None, "byterange": None, "discontinuity": False, "gap": False, "pdt": None}
    cur_map, cur_key, cur_iv = None, None, None
    prev = None
    for l in lines:
        if l.startswith("#EXT-X-VERSION:"):
            info["version"] = int(l.split(":", 1)[1])
        elif l.startswith("#EXT-X-TARGETDURATION:"):
            info["target_duration"] = int(l.split(":", 1)[1])
        elif l.startswith("#EXT-X-MEDIA-SEQUENCE:"):
            info["media_sequence"] = int(l.split(":", 1)[1])
        elif l.startswith("#EXT-X-DISCONTINUITY-SEQUENCE:"):
            info["discontinuity_sequence"] = int(l.split(":", 1)[1])
        elif l.startswith("#EXT-X-PLAYLIST-TYPE:"):
            info["playlist_type"] = l.split(":", 1)[1]
        elif l == "#EXT-X-ENDLIST":
            info["endlist"] = True
        elif l.startswith("#EXTINF:"):
            pending["duration"] = float(l.split(":", 1)[1].split(",", 1)[0].strip())
        elif l.startswith("#EXT-X-BYTERANGE:"):
            pending["byterange"] = _range(l.split(":", 1)[1])
        elif l == "#EXT-X-DISCONTINUITY":
            pending["discontinuity"] = True
        elif l.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pending["pdt"] = l.split(":", 1)[1]
        elif l.startswith("#EXT-X-MAP:"):
            a = attrs(l.split(":", 1)[1])
            br = _range(a["BYTERANGE"]) if "BYTERANGE" in a else None
            uri = urljoin(base, a["URI"])
            cur_map = {"uri": uri, "byterange": [br[1] or 0, br[0]] if br else None}
        elif l.startswith("#EXT-X-KEY:"):
            a = attrs(l.split(":", 1)[1])
            cur_key = None if a.get("METHOD") == "NONE" else a.get("METHOD")
            cur_iv = ("0x%032x" % int(a["IV"], 16)) if cur_key and "IV" in a else None
        elif l.startswith("#"):
            continue
        else:
            if pending["duration"] is None:
                raise HlsError("segment %s has no EXTINF" % l)
            uri = urljoin(base, l)
            br = None
            if pending["byterange"]:
                n, o = pending["byterange"]
                if o is None:
                    o = 0
                br = [o, n]
            seq = info["media_sequence"] + len(segs)
            segs.append({"sequence": seq, "uri": uri, "iv": cur_iv,
                         "duration": str(Fraction(pending["duration"])),
                         "byterange": br, "map": cur_map, "key": cur_key, "discontinuity": pending["discontinuity"],
                         "gap": pending["gap"], "program_date_time": pending["pdt"]})
            prev = segs[-1]
            pending = {"duration": None, "byterange": None, "discontinuity": False, "gap": False, "pdt": None}
    total = sum((Fraction(s["duration"]) for s in segs), Fraction(0))
    playable = sum((Fraction(s["duration"]) for s in segs if not s["gap"]), Fraction(0))
    return {**info, "segments": segs, "duration": str(total), "playable_duration": str(playable),
            "discontinuities": sum(s["discontinuity"] for s in segs), "gaps": sum(s["gap"] for s in segs)}
