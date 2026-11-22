"""HTTP Live Streaming playlists (RFC 8216): media and master playlists."""
from fractions import Fraction
from urllib.parse import urljoin


class HlsError(ValueError):
    pass


def attrs(text):
    """Parse an attribute list (RFC 8216, section 4.2).

    Attributes are comma-separated, but commas inside quoted-string values
    are ordinary characters, so splitting on every comma corrupts values
    such as CODECS="avc1.640028,mp4a.40.2".
    """
    out = {}
    quoted, part = False, []
    for ch in text:
        if ch == '"':
            quoted = not quoted
            part.append(ch)
        elif ch == "," and not quoted:
            _attr(part, out)
            part = []
        else:
            part.append(ch)
    _attr(part, out)
    return out


def _attr(part, out):
    part = "".join(part).strip()
    if not part or "=" not in part:
        return
    k, v = part.split("=", 1)
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
    out[k.strip()] = v


def url_resolve(base, uri):
    """Resolve a playlist URI against the URL the playlist was fetched from."""
    return urljoin(base, uri) if base else uri


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
                             "resolution": a.get("RESOLUTION"),
                             "uri": url_resolve(base, uri) if uri else None})
        elif l.startswith("#EXT-X-MEDIA:"):
            media += 1
    return {"type": "master", "variants": variants, "renditions": media}


def _media(lines, base):
    info = {"type": "media", "version": 1, "target_duration": None, "media_sequence": 0,
            "discontinuity_sequence": 0, "endlist": False, "playlist_type": None}
    segs = []
    pending = {"duration": None, "byterange": None, "discontinuity": False, "gap": False, "pdt": None}
    cur_map, cur_key, cur_iv = None, None, None
    # The resolved URI and byterange of the previous segment, needed to
    # continue an EXT-X-BYTERANGE that omits its offset (RFC 8216 4.3.2.2).
    prev_uri, prev_range = None, None
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
            # The decimal-integer/decimal-floating-point string is turned
            # straight into a Fraction so e.g. 6.006 stays 3003/500 instead
            # of picking up binary float error.
            pending["duration"] = Fraction(l.split(":", 1)[1].split(",", 1)[0].strip())
        elif l.startswith("#EXT-X-BYTERANGE:"):
            pending["byterange"] = _range(l.split(":", 1)[1])
        elif l == "#EXT-X-DISCONTINUITY":
            pending["discontinuity"] = True
        elif l == "#EXT-X-GAP":
            pending["gap"] = True
        elif l.startswith("#EXT-X-PROGRAM-DATE-TIME:"):
            pending["pdt"] = l.split(":", 1)[1]
        elif l.startswith("#EXT-X-MAP:"):
            a = attrs(l.split(":", 1)[1])
            br = _range(a["BYTERANGE"]) if "BYTERANGE" in a else None
            uri = url_resolve(base, a["URI"])
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
            uri = url_resolve(base, l)
            br = None
            if pending["byterange"]:
                length, offset = pending["byterange"]
                if offset is None:
                    # No offset: start immediately after the previous segment,
                    # which must be a sub-range of the same resource.
                    if prev_range is None or uri != prev_uri:
                        raise HlsError(
                            "byterange of %s omits its offset but the previous "
                            "segment is not a sub-range of the same resource" % l)
                    offset = prev_range[0] + prev_range[1]
                br = [offset, length]
            seq = info["media_sequence"] + len(segs)
            # AES-128 without an IV attribute derives it from the media
            # sequence number (RFC 8216, section 5.2).
            iv = cur_iv
            if iv is None and cur_key == "AES-128":
                iv = "0x%032x" % seq
            segs.append({"sequence": seq, "uri": uri, "iv": iv,
                         "duration": str(pending["duration"]),
                         "byterange": br, "map": cur_map, "key": cur_key,
                         "discontinuity": pending["discontinuity"],
                         "gap": pending["gap"], "program_date_time": pending["pdt"]})
            prev_uri, prev_range = uri, br
            pending = {"duration": None, "byterange": None, "discontinuity": False,
                       "gap": False, "pdt": None}
    total = sum((Fraction(s["duration"]) for s in segs), Fraction(0))
    playable = sum((Fraction(s["duration"]) for s in segs if not s["gap"]), Fraction(0))
    return {**info, "segments": segs, "duration": str(total), "playable_duration": str(playable),
            "discontinuities": sum(s["discontinuity"] for s in segs),
            "gaps": sum(s["gap"] for s in segs)}
