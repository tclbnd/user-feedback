"""Tag summary used by the episode catalogue."""
from . import frames as fr
from .id3 import read_tag


def summarise(data):
    header, frames = read_tag(data)
    major = int(header["version"].split(".")[1])
    out = {"version": header["version"], "size": header["size"],
           "text": {}, "comments": [], "user": {}}
    for f in frames:
        if f.id == "COMM":
            out["comments"].append(fr.comment(f.body))
        elif f.id == "TXXX":
            desc, value = fr.user_text(f.body)
            out["user"][desc] = value
        elif f.id == "TCON":
            out["text"].setdefault(f.id, []).extend(fr.genre(f.body, major))
        elif f.id.startswith("T"):
            out["text"].setdefault(f.id, []).extend(fr.text(f.body, major))
    return out
