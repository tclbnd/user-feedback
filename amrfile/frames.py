"""AMR storage format frames."""

# bytes per frame (header byte included) by frame type
NB_SIZE = [13, 14, 16, 18, 20, 20, 27, 32, 6]
NB_KBPS = ["4.75", "5.15", "5.9", "6.7", "7.4", "7.95", "10.2", "12.2"]


class AmrError(ValueError):
    pass


def frame_size(ft):
    if ft < len(NB_SIZE):
        return NB_SIZE[ft]
    raise AmrError(f"unexpected frame type {ft}")


def read(data):
    if not data.startswith(b"#!AMR"):
        raise AmrError("not an AMR file")
    pos = 6
    frames = []
    while pos < len(data):
        h = data[pos]
        ft = (h >> 3) & 0x0F
        size = frame_size(ft)
        frames.append({"ft": ft, "quality": bool(h & 4), "size": size})
        pos += size
    return {"codec": "amr-nb", "sample_rate": 8000, "channels": 1}, frames
