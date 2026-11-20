"""Stream summary and per-frame luma level."""
from . import stream


def probe(data):
    params, x_params, start = stream.header(data)
    width, height = int(params["W"]), int(params["H"])
    colorspace = params.get("C", "420jpeg")
    plane_list = stream.planes(width, height, colorspace)
    frame_bytes = stream.frame_size(width, height, colorspace)
    fs = stream.frames(data, start, width, height, colorspace)
    return {
        "width": width,
        "height": height,
        "fps": params.get("F", "0:0").replace(":", "/"),
        "interlace": params.get("I", "?"),
        "aspect": params.get("A", "0:0").replace(":", "/"),
        "colorspace": colorspace,
        "color_range": {"FULL": "full", "LIMITED": "limited"}.get(
            x_params.get("COLORRANGE")
        ),
        "bit_depth": stream.bit_depth(colorspace),
        "planes": [list(p) for p in plane_list],
        "frame_size": frame_bytes,
        "frames": len(fs),
        "mean_luma": [
            stream.luma_mean(f, width, height, colorspace) for f in fs
        ],
    }


def frame(data, index):
    params, _, start = stream.header(data)
    width, height = int(params["W"]), int(params["H"])
    colorspace = params.get("C", "420jpeg")
    return stream.extract(data, start, width, height, colorspace, index)
