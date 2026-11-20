"""Pick the encoding ladder rungs a source can feed."""
LADDER = [(3840, 2160), (2560, 1440), (1920, 1080), (1280, 720), (960, 540), (640, 360)]


def rungs(width, height):
    return [f"{w}x{h}" for w, h in LADDER if w <= width and h <= height]


def needs_deinterlace(info):
    return not info.get("frame_mbs_only", True)
