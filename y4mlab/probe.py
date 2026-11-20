"""Stream summary and per-frame luma level."""
from . import stream


def probe(data):
    params, start = stream.header(data)
    width, height = int(params["W"]), int(params["H"])
    colorspace = params.get("C", "420jpeg")
    plane_list = stream.planes(width, height, colorspace)
    frame_bytes = sum(w * h for w, h in plane_list)
    fs = stream.frames(data, start, frame_bytes)
    luma = width * height
    return {
        "width": width,
        "height": height,
        "fps": params.get("F", "0:0").replace(":", "/"),
        "interlace": params.get("I", "?"),
        "aspect": params.get("A", "0:0").replace(":", "/"),
        "colorspace": colorspace,
        "color_range": {"FULL": "full", "LIMITED": "limited"}.get(params.get("X", "").partition("=")[2]),
        "bit_depth": 8,
        "planes": [list(p) for p in plane_list],
        "frame_size": frame_bytes,
        "frames": len(fs),
        "mean_luma": [round(sum(f[:luma]) / luma, 3) for f in fs],
    }


def frame(data, index):
    params, start = stream.header(data)
    width, height = int(params["W"]), int(params["H"])
    plane_list = stream.planes(width, height, params.get("C", "420jpeg"))
    return stream.extract(data, start, sum(w * h for w, h in plane_list), index)
