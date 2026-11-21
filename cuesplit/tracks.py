"""Track start, end and length in samples, relative to each track's file."""


def layout(album, lengths=None):
    lengths = lengths or {}
    flat = [(f["name"], t) for f in album["files"] for t in f["tracks"]]
    out = []
    for i, (name, t) in enumerate(flat):
        start = t["indexes"].get(0, t["indexes"].get(1))
        if i + 1 < len(flat):
            nt = flat[i + 1][1]
            end = nt["indexes"].get(0, nt["indexes"].get(1))
        else:
            end = lengths.get(name)
        out.append({
            "number": t["number"],
            "title": t["title"],
            "performer": t["performer"],
            "file": name,
            "start": start,
            "end": end,
            "length": end - start if end is not None else None,
            "pregap": 0,
        })
    return out
