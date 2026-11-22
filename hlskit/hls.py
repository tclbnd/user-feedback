"""HTTP Live Streaming playlists (RFC 8216): media and master playlists."""
import re
from fractions import Fraction

_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_QUERY = re.compile(r"[?#]")


class HlsError(ValueError):
    pass


def attrs(text):
    """Parse an RFC 8216 attribute list.

    Values may be quoted, and quoted values may contain commas (CODECS is the
    usual case), so the list cannot simply be split on commas.
    """
    out = {}
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i] in " \t,":
            i += 1
        if i >= n:
            break
        eq = text.find("=", i)
        if eq == -1:
            break
        key = text[i:eq].strip()
        i = eq + 1
        if i < n and text[i] == '"':
            end = text.find('"', i + 1)
            if end == -1:
                value, i = text[i + 1:], n
            else:
                value, i = text[i + 1:end], end + 1
        else:
            end = i
            while end < n and text[end] != ",":
                end += 1
            value, i = text[i:end].strip(), end
        if key:
            out[key] = value
    return out


def _remove_dot_segments(path):
    """RFC 3986 section 5.2.4, applied to the path of a merged reference."""
    absolute = path.startswith("/")
    out = []
    for seg in path.split("/"):
        if seg == ".":
            continue
        if seg == "..":
            if out and out[-1] not in ("", ".."):
                out.pop()
            elif not absolute:
                out.append("..")
            continue
        out.append(seg)
    resolved = "/".join(out)
    if absolute:
        resolved = "/" + resolved.lstrip("/")
    if (path.endswith("/.") or path.endswith("/..")) and not resolved.endswith("/"):
        resolved += "/"
    if absolute and not resolved:
        resolved = "/"
    return resolved


def urljoin(base, uri):
    """Resolve *uri* against *base* (RFC 3986), or return it unchanged if
    it is already absolute or no base is given."""
    uri = uri.strip()
    if not base or _SCHEME.match(uri):
        return uri
    if uri.startswith("//"):
        scheme = _SCHEME.match(base)
        return (scheme.group(0) if scheme else "") + uri

    scheme = _SCHEME.match(base)
    scheme = scheme.group(0) if scheme else ""
    rest = base[len(scheme):]
    if rest.startswith("//"):
        slash = rest.find("/", 2)
        if slash == -1:
            prefix, path = scheme + rest, ""
        else:
            prefix, path = scheme + rest[:slash], rest[slash:]
    else:
        prefix, path = scheme, rest
    path = _QUERY.split(path, 1)[0]

    cut = len(uri)
    for ch in "?#":
        pos = uri.find(ch)
        if pos != -1:
            cut = min(cut, pos)
    ref, suffix = uri[:cut], uri[cut:]
    if not ref:
        # An empty reference keeps the base path (RFC 3986 5.2.2).
        return prefix + path + suffix
    if ref.startswith("/"):
        target = ref
    else:
        slash = path.rfind("/")
        target = (path[:slash + 1] if slash != -1 else "") + ref
        if not path:
            # A base with no path has an implicit "/" (RFC 3986 5.3).
            target = "/" + ref
    return prefix + _remove_dot_segments(target) + suffix


def _range(text):
    """Parse <length>[@<offset>] into (length, offset or None)."""
    text = text.strip().strip('"')
    if "@" in text:
        n, o = text.split("@", 1)
        return int(n), int(o)
    return int(text), None


def _duration(text):
    value = text.split(",", 1)[0].strip()
    if not value:
        raise HlsError("EXTINF has no duration")
    try:
        return Fraction(value)
    except (ValueError, ZeroDivisionError):
        raise HlsError("invalid EXTINF duration: %r" % value)


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
            variants.append({"bandwidth": int(a["BANDWIDTH"]) if "BANDWIDTH" in a else None,
                             "codecs": a.get("CODECS"), "resolution": a.get("RESOLUTION"),
                             "uri": urljoin(base, uri) if uri else None})
        elif l.startswith("#EXT-X-MEDIA:"):
            media += 1
    return {"type": "master", "variants": variants, "renditions": media}


def _media(lines, base):
    info = {"type": "media", "version": 1, "target_duration": None, "media_sequence": 0,
            "discontinuity_sequence": 0, "endlist": False, "playlist_type": None}
    segs = []
    skipped = 0
    pending = {"duration": None, "byterange": None, "discontinuity": False, "gap": False, "pdt": None}
    cur_map, cur_key, cur_iv = None, None, None
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
        elif l.startswith("#EXT-X-SKIP:"):
            a = attrs(l.split(":", 1)[1])
            skipped = int(a.get("SKIPPED-SEGMENTS", 0))
        elif l.startswith("#EXTINF:"):
            pending["duration"] = _duration(l.split(":", 1)[1])
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
            cur_map = {"uri": urljoin(base, a["URI"]),
                       "byterange": [br[1] if br[1] is not None else 0, br[0]] if br else None}
        elif l.startswith("#EXT-X-KEY:"):
            a = attrs(l.split(":", 1)[1])
            if a.get("METHOD") == "NONE":
                cur_key, cur_iv = None, None
            else:
                cur_key = a.get("METHOD")
                cur_iv = int(a["IV"], 16) if "IV" in a else None
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
                    if prev_range is not None and prev_uri == uri:
                        o = prev_range[0] + prev_range[1]
                    else:
                        o = 0
                br = [o, n]
            seq = info["media_sequence"] + skipped + len(segs)
            if cur_key == "AES-128":
                iv = cur_iv if cur_iv is not None else seq
                iv = "0x%032x" % iv
            else:
                iv = None
            segs.append({"sequence": seq, "uri": uri, "iv": iv,
                         "duration": str(pending["duration"]),
                         "byterange": br, "map": cur_map, "key": cur_key,
                         "discontinuity": pending["discontinuity"], "gap": pending["gap"],
                         "program_date_time": pending["pdt"]})
            prev_uri, prev_range = uri, br
            pending = {"duration": None, "byterange": None, "discontinuity": False, "gap": False, "pdt": None}
    total = sum((Fraction(s["duration"]) for s in segs), Fraction(0))
    playable = sum((Fraction(s["duration"]) for s in segs if not s["gap"]), Fraction(0))
    return {**info, "segments": segs, "duration": str(total), "playable_duration": str(playable),
            "discontinuities": sum(s["discontinuity"] for s in segs), "gaps": sum(s["gap"] for s in segs)}
