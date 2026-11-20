"""Output file names for split tracks."""
import re

_BAD = re.compile(r'[\\/:*?"<>|]')


def safe(text):
    return _BAD.sub("_", text or "").strip(" .") or "untitled"


def track_name(track, pattern="{number:02d} - {title}.flac"):
    return pattern.format(number=track["number"], title=safe(track["title"]),
                          performer=safe(track.get("performer")))


def unique(names):
    seen, out = {}, []
    for n in names:
        if n in seen:
            seen[n] += 1
            stem, dot, ext = n.rpartition(".")
            n = f"{stem} ({seen[n]}).{ext}" if dot else f"{n} ({seen[n]})"
        else:
            seen[n] = 0
        out.append(n)
    return out
