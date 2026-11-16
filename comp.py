#!/usr/bin/env python3
"""Frame compositor: alpha subtitles/logos over video, in linear light.

Input (JSON)::

    {
      "w": int, "h": int,
      "base": [[[r, g, b], ...], ...],          # W x H, sRGB encoded
      "base_pts": int,
      "layers": [
        {"x": int, "y": int, "w": int, "h": int,
         "pixels": [[r, g, b, a], ...],         # w*h, row-major
         "premultiplied": bool, "pts": int},
        ...
      ]
    }

Optional, all backwards compatible:

* ``max_value`` / ``bit_depth`` on the document or a layer: sample range for
  high bit depth material (defaults to 8-bit, i.e. 255).
* ``pts`` may be a start time or ``[start, end]``; ``end_pts``/``duration``
  further restrict a layer to its valid display interval.  A layer is only
  composited when the base PTS lies inside that interval.
* ``mask``: w*h coverage samples for the layer, multiplied into alpha.
* ``clip``: ``[x, y, w, h]`` destination rectangle limiting where a layer may
  draw; ``crop`` / ``sx, sy, cw, ch`` selects a sub-rectangle of a padded
  source image while keeping it in place.
* ``regions``/``tiles``: a list of independently positioned/cropped pixel
  blocks that together form one layer; overlapping pixels are taken once.
* ``block``/``tile`` on the document (or ``COMP_BLOCK``) selects the
  processing chunk size; results are bit-identical for every chunk size.

Layers are composited bottom-to-top in array order.  Every destination pixel
is blended at most once per layer, so overlapping tiles or repeated coverage
never accumulate twice.  Colour is decoded to non-premultiplied sRGB, moved to
linear light, combined with the source-over operator, converted back to sRGB
and quantised with round-half-up.  The effective coverage is exported
alongside the colour so the intermediate alpha can be verified.
"""

import json
import math
import os
import sys


GAMMA = 2.2

# ``pow`` tables keyed by sample range.  The values are identical to calling
# pow() directly, so caching cannot change the result of a computation.
_DECODE_TABLE = {}


def _max_value(obj, inherited=255.0):
    """Decode an optional integer sample range without assuming 8 bit."""
    if not isinstance(obj, dict):
        return inherited
    for key in ("max_value", "maxval", "white_level", "white", "peak"):
        value = obj.get(key)
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                continue
            if value > 0.0:
                return value
    for key in ("bit_depth", "bits", "depth", "bpc"):
        value = obj.get(key)
        if value is not None:
            try:
                depth = int(value)
            except (TypeError, ValueError):
                continue
            if depth > 0:
                return float((1 << depth) - 1)
    return inherited


def _unit(value, maximum, clamp=True):
    value = float(value) / maximum
    if clamp:
        if value < 0.0:
            return 0.0
        if value > 1.0:
            return 1.0
    return value


def _linear(encoded):
    """sRGB-ish encoded component -> linear light component."""
    if encoded <= 0.0:
        return 0.0
    return encoded ** GAMMA


def _encoded(linear):
    if linear <= 0.0:
        return 0.0
    return linear ** (1.0 / GAMMA)


def _decode_table(maximum):
    """Linear values indexed by integer sample, or None when too large."""
    key = float(maximum)
    if key in _DECODE_TABLE:
        return _DECODE_TABLE[key]
    limit = int(key)
    if limit < 1 or limit > 65535 or limit != key:
        _DECODE_TABLE[key] = None
        return None
    table = [_linear(i / key) for i in range(limit + 1)]
    _DECODE_TABLE[key] = table
    return table


def _decode(value, maximum, table):
    """Encoded sample -> linear component (table result is bit-identical)."""
    if table is not None:
        index = int(value)
        if 0 <= index < len(table) and index == value:
            return table[index]
    return _linear(_unit(value, maximum))


def _round_half_up(linear):
    """Linear [0, 1] -> 8-bit code, round half up (not banker's rounding)."""
    value = _encoded(linear) * 255.0
    if value <= 0.0:
        return 0
    if value >= 255.0:
        return 255
    return int(math.floor(value + 0.5))


def _quantise_unit(value):
    """Unit-interval coverage -> 8-bit code, round half up."""
    value *= 255.0
    if value <= 0.0:
        return 0
    if value >= 255.0:
        return 255
    return int(math.floor(value + 0.5))


def _layer_interval(layer):
    """Display interval of a layer as (start, end) in PTS units.

    ``pts`` is the overlay's start time (the time it first becomes visible).
    ``[start, end]``, ``end_pts``, ``out`` or ``duration`` narrows the
    interval; an open end means the overlay stays valid for every later frame.
    """
    start = None
    for key in ("pts", "start_pts", "pts_in", "in", "start", "begin"):
        if layer.get(key) is not None:
            start = layer[key]
            break
    end = None
    for key in ("end_pts", "pts_out", "pts_end", "out", "end", "finish"):
        if layer.get(key) is not None:
            end = layer[key]
            break
    duration = None
    for key in ("duration", "duration_pts", "length"):
        if layer.get(key) is not None:
            duration = layer[key]
            break
    for key in ("range", "pts_range", "interval", "window"):
        span = layer.get(key)
        if isinstance(span, (list, tuple)) and len(span) >= 2:
            if start is None:
                start = span[0]
            if end is None:
                end = span[1]
            break
    if isinstance(start, (list, tuple)) and len(start) >= 2:
        if end is None:
            end = start[1]
        start = start[0]
    if start is None:
        return None, None
    try:
        start = float(start)
    except (TypeError, ValueError):
        return None, None
    if end is None and duration is not None:
        try:
            end = start + float(duration)
        except (TypeError, ValueError):
            end = None
    if end is not None:
        try:
            end = float(end)
        except (TypeError, ValueError):
            end = None
    return start, end


def _layer_visible(layer, base_pts):
    start, end = _layer_interval(layer)
    if start is None or base_pts is None:
        return True
    try:
        frame = float(base_pts)
    except (TypeError, ValueError):
        return True
    if frame < start:
        return False
    if end is not None and frame >= end:
        return False
    return True


def _as_rows(base, width, height, count):
    """Accept the documented row-major base or a flat pixel list."""
    if len(base) == height and all(
            isinstance(row, (list, tuple)) and len(row) == width
            and all(isinstance(px, (list, tuple)) for px in row)
            for row in base):
        return base
    if (len(base) == count and base
            and isinstance(base[0], (list, tuple))):
        return [base[y * width:(y + 1) * width] for y in range(height)]
    # Interleaved channel samples (RGB or RGBA).
    if (isinstance(base, list) and base
            and not isinstance(base[0], (list, tuple))):
        channels = len(base) // count if count else 0
        if channels in (3, 4) and channels * count == len(base):
            return [
                [base[(y * width + x) * channels:
                      (y * width + x) * channels + channels]
                 for x in range(width)]
                for y in range(height)
            ]
    raise ValueError("base dimensions do not match w/h")


def _layer_pixels(pixels, lw, lh):
    """Normalise a layer's pixel array to a flat, row-major pixel sequence."""
    count = lw * lh
    if len(pixels) == lh and all(
            isinstance(row, (list, tuple)) and len(row) == lw
            for row in pixels):
        return [px for row in pixels for px in row]
    if len(pixels) == count:
        return pixels
    # Interleaved RGBA samples.
    if pixels and not isinstance(pixels[0], (list, tuple)):
        if len(pixels) == count * 4:
            return [pixels[i * 4:i * 4 + 4] for i in range(count)]
    return None


def _clamp01(value):
    if value < 0.0:
        return 0.0
    if value > 1.0:
        return 1.0
    return value


def _rect_of(value, lw, lh):
    """Normalise a rect given as dict or list into (x, y, w, h)."""
    if value is None:
        return None
    if isinstance(value, dict):
        x = int(value.get("x", value.get("sx", 0)))
        y = int(value.get("y", value.get("sy", 0)))
        w = int(value.get("w", value.get("cw", lw - x)))
        h = int(value.get("h", value.get("ch", lh - y)))
        return x, y, w, h
    if not isinstance(value, (list, tuple)):
        return None
    if len(value) == 2:
        x, y = int(value[0]), int(value[1])
        return x, y, lw - x, lh - y
    if len(value) >= 4:
        return tuple(int(v) for v in value[:4])
    return None


def _clip_of(layer):
    """Optional destination clip rectangle, relative to the layer origin."""
    lw = int(layer.get("w", 0))
    lh = int(layer.get("h", 0))
    for key in ("clip", "clip_rect", "bounds", "draw_rect", "region"):
        if layer.get(key) is not None:
            return _rect_of(layer[key], lw, lh)
    if "clip_x" in layer or "clip_y" in layer:
        x = int(layer.get("clip_x", 0))
        y = int(layer.get("clip_y", 0))
        return (
            x,
            y,
            int(layer.get("clip_w", lw - x)),
            int(layer.get("clip_h", lh - y)),
        )
    return None


def _crop_of(layer):
    """Optional source sub-rectangle of a padded overlay.

    A crop keeps the overlay's pixels in place: the sub-rect ``[sx, sy, w, h]``
    is drawn at ``(layer.x + sx, layer.y + sy)`` exactly as if the whole
    (padded) image had been submitted.
    """
    lw = int(layer.get("w", 0))
    lh = int(layer.get("h", 0))
    for key in ("crop", "source_rect", "src_rect"):
        if layer.get(key) is not None:
            return _rect_of(layer[key], lw, lh)
    if any(key in layer for key in ("sx", "sy", "cw", "ch")):
        sx = int(layer.get("sx", 0))
        sy = int(layer.get("sy", 0))
        return (
            sx,
            sy,
            int(layer.get("cw", lw - sx)),
            int(layer.get("ch", lh - sy)),
        )
    return None


def _mask_of(layer, lw, lh):
    """Coverage mask for a layer as (samples, mask_w, mask_h, max), or None.

    A mask is one coverage sample per source pixel; it may be given for the
    whole layer or for just the clipped sub-rectangle.
    """
    mask = layer.get("mask", layer.get("alpha_mask"))
    if mask is None:
        return None
    mask_max = _max_value(layer, 255.0)
    if isinstance(mask, dict):
        mask_max = _max_value(mask, mask_max)
        mw = int(mask.get("w", lw))
        mh = int(mask.get("h", lh))
        mask = mask.get("pixels", mask.get("data", mask.get("values")))
    else:
        mw, mh = lw, lh
    if not isinstance(mask, list) or mw <= 0 or mh <= 0 or not mask:
        return None
    if isinstance(mask[0], (list, tuple)):
        values = []
        for row in mask:
            values.extend(row)
        mask = values
    if len(mask) < mw * mh:
        return None
    return mask, mw, mh, mask_max


def _source_over(pixel, mask_value, premultiplied, maximum, table):
    """Decode one RGBA source sample into (linear rgb, alpha).

    Premultiplied samples are un-premultiplied before the transfer function.
    Fully transparent pixels have undefined colour and return ``None`` so they
    behave exactly as transparent black.  The optional mask sample scales the
    source alpha without touching its colour.
    """
    a = 1.0
    if len(pixel) >= 4:
        a = _unit(pixel[3], maximum)
    if a <= 0.0:
        return None
    if premultiplied:
        # Divide by the source alpha (before any mask) in the encoded domain;
        # re-clamping guards malformed samples whose rgb exceeds the alpha.
        r = _clamp01(_unit(pixel[0], maximum, clamp=False) / a)
        g = _clamp01(_unit(pixel[1], maximum, clamp=False) / a)
        b = _clamp01(_unit(pixel[2], maximum, clamp=False) / a)
        rgb = (_linear(r), _linear(g), _linear(b))
    else:
        rgb = (
            _decode(pixel[0], maximum, table),
            _decode(pixel[1], maximum, table),
            _decode(pixel[2], maximum, table),
        )
    if mask_value is not None:
        a *= _clamp01(mask_value)
        if a <= 0.0:
            return None
    return rgb, a


def _block_size(data, block):
    for candidate in (block, data.get("block"), data.get("tile"),
                      data.get("tile_size"), data.get("chunk"),
                      data.get("chunk_size"), os.environ.get("COMP_BLOCK")):
        if candidate is None:
            continue
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return 0


def _region_of(layer, layer_w, layer_h, base_max):
    """Describe one drawable region: geometry, samples and decode settings."""
    lw = int(layer.get("w", layer_w))
    lh = int(layer.get("h", layer_h))
    if lw <= 0 or lh <= 0:
        return None
    pixels = layer.get("pixels")
    if pixels is None:
        return None
    pixels = _layer_pixels(pixels, lw, lh)
    if pixels is None:
        return None
    maximum = _max_value(layer, base_max)
    return {
        "x": int(layer.get("x", 0)),
        "y": int(layer.get("y", 0)),
        "w": lw,
        "h": lh,
        "pixels": pixels,
        "max": maximum,
        "table": _decode_table(maximum),
        "premultiplied": bool(layer.get("premultiplied", False)),
        "clip": _clip_of(layer),
        "crop": _crop_of(layer),
        "mask": _mask_of(layer, lw, lh),
    }


def _layer_regions(layer, base_max):
    """All drawable regions of a layer.

    A layer may carry a single pixel array or a list of ``regions``/``tiles``
    (for example separately cropped source blocks).  Regions are drawn in
    order and never composite the same destination pixel twice.
    """
    regions = None
    for key in ("regions", "tiles", "parts", "blocks"):
        if isinstance(layer.get(key), list):
            regions = layer[key]
            break
    if regions is None:
        region = _region_of(layer, 0, 0, base_max)
        return None if region is None else [region]

    merged = dict(layer)
    merged.pop("regions", None)
    merged.pop("tiles", None)
    merged.pop("parts", None)
    merged.pop("blocks", None)
    merged.pop("pixels", None)
    result = []
    for entry in regions:
        if not isinstance(entry, dict):
            continue
        region_layer = dict(merged)
        region_layer.update(entry)
        region = _region_of(region_layer, 0, 0, base_max)
        if region is not None:
            result.append(region)
    return result


def composite(data, block=None):
    """Composite ``data``; ``block`` selects an optional destination tile size.

    Tiling only changes the traversal order, never the arithmetic: a pixel's
    base and every covering region are evaluated within the same tile, so any
    tile size yields bit-identical output.  Within one layer, overlapping
    regions are deduplicated so no pixel is blended twice.
    """
    width = int(data["w"])
    height = int(data["h"])
    count = width * height
    if width <= 0 or height <= 0:
        return {"pixels": [], "alpha": []}

    base_max = _max_value(data)
    base = _as_rows(data["base"], width, height, count)
    base_table = _decode_table(base_max)
    base_pts = data.get("base_pts")

    layers = []
    for layer in data.get("layers", ()):
        if not _layer_visible(layer, base_pts):
            continue
        regions = _layer_regions(layer, base_max)
        if regions:
            layers.append(regions)

    # Premultiplied linear canvas plus a separate coverage channel.  Colour
    # and alpha stay floating point until the final quantisation so that no
    # intermediate rounding can bias the result.
    canvas = [0.0] * (count * 3)
    alpha = [0.0] * count

    step = _block_size(data, block)
    if step <= 0:
        step = max(width, height)
    step = max(1, min(step, max(width, height)))

    for ty in range(0, height, step):
        ty1 = min(height, ty + step)
        for tx in range(0, width, step):
            tx1 = min(width, tx + step)

            for y in range(ty, ty1):
                row = base[y]
                for x in range(tx, tx1):
                    pixel = row[x]
                    index = y * width + x
                    offset = index * 3
                    a = 1.0
                    if len(pixel) >= 4:
                        a = _unit(pixel[3], base_max)
                    if a <= 0.0:
                        canvas[offset] = 0.0
                        canvas[offset + 1] = 0.0
                        canvas[offset + 2] = 0.0
                        alpha[index] = 0.0
                        continue
                    canvas[offset] = (
                        _decode(pixel[0], base_max, base_table) * a
                    )
                    canvas[offset + 1] = (
                        _decode(pixel[1], base_max, base_table) * a
                    )
                    canvas[offset + 2] = (
                        _decode(pixel[2], base_max, base_table) * a
                    )
                    alpha[index] = a

            for regions in layers:
                covered = None if len(regions) < 2 else set()
                for region in regions:
                    _composite_region(
                        region, canvas, alpha, covered,
                        width, height, tx, tx1, ty, ty1,
                    )

    out_pixels = []
    out_alpha = []
    for index in range(count):
        a = alpha[index]
        offset = index * 3
        if a <= 0.0:
            # Undefined colour at zero coverage is normalised to transparent
            # black rather than leaking the samples' uninitialised channels.
            out_pixels.append([0, 0, 0])
            out_alpha.append(0)
            continue
        inv_a = 1.0 / a
        out_pixels.append([
            _round_half_up(canvas[offset] * inv_a),
            _round_half_up(canvas[offset + 1] * inv_a),
            _round_half_up(canvas[offset + 2] * inv_a),
        ])
        out_alpha.append(_quantise_unit(a))
    return {"pixels": out_pixels, "alpha": out_alpha}


def _composite_region(region, canvas, alpha, covered,
                      width, height, tx, tx1, ty, ty1):
    """Source-over one region onto the premultiplied linear canvas."""
    lx = region["x"]
    ly = region["y"]
    lw = region["w"]
    lh = region["h"]
    pixels = region["pixels"]
    maximum = region["max"]
    table = region["table"]
    premultiplied = region["premultiplied"]
    mask = region["mask"]
    clip = region["clip"]
    crop = region["crop"]

    # A source crop selects which samples are used; the selected sub-rectangle
    # stays at its original place inside the layer.
    if crop is not None:
        sx, sy, used_w, used_h = crop
        draw_x = lx + sx
        draw_y = ly + sy
    else:
        sx = sy = 0
        used_w, used_h = lw, lh
        draw_x, draw_y = lx, ly

    # Restrict drawing to the used source area, the optional destination clip,
    # the canvas and this tile.  Each destination pixel is therefore visited at
    # most once per region.
    x0 = max(tx, draw_x, 0)
    x1 = min(tx1, draw_x + used_w, width)
    y0 = max(ty, draw_y, 0)
    y1 = min(ty1, draw_y + used_h, height)
    if clip is not None:
        cx, cy, cw, ch = clip
        x0 = max(x0, lx + cx)
        x1 = min(x1, lx + cx + cw)
        y0 = max(y0, ly + cy)
        y1 = min(y1, ly + cy + ch)
    if x0 >= x1 or y0 >= y1:
        return

    for dy in range(y0, y1):
        source_row = (dy - ly) * lw
        dest_row = dy * width
        for dx in range(x0, x1):
            index = dest_row + dx
            if covered is not None:
                # Overlapping regions of the same layer must not blend twice;
                # the first region covering a destination pixel wins.
                if index in covered:
                    continue
            source_index = source_row + (dx - lx)
            mask_value = None
            if mask is not None:
                mask_values, mask_w, mask_h, mask_max = mask
                if mask_w == lw and mask_h == lh:
                    mask_index = source_index
                else:
                    mask_index = ((dy - ly) % mask_h) * mask_w \
                        + ((dx - lx) % mask_w)
                mask_value = _unit(mask_values[mask_index], mask_max)
            decoded = _source_over(
                pixels[source_index], mask_value, premultiplied, maximum,
                table,
            )
            if decoded is None:
                # A transparent (or fully masked) sample contributes nothing,
                # so it does not claim the destination: another region of the
                # same layer may still provide coverage here.
                continue
            src_rgb, src_a = decoded
            if covered is not None:
                covered.add(index)
            # Exactly one source-over evaluation per destination pixel per
            # layer: later layers replace earlier composites, they never
            # accumulate with the same layer's own coverage.
            inv_a = 1.0 - src_a
            offset = index * 3
            canvas[offset] = src_rgb[0] * src_a + canvas[offset] * inv_a
            canvas[offset + 1] = (
                src_rgb[1] * src_a + canvas[offset + 1] * inv_a
            )
            canvas[offset + 2] = (
                src_rgb[2] * src_a + canvas[offset + 2] * inv_a
            )
            alpha[index] = src_a + alpha[index] * inv_a


def main(argv=None):
    argv = sys.argv if argv is None else argv
    if len(argv) != 3:
        raise SystemExit("usage: comp.py input.json output.json")
    with open(argv[1], "r", encoding="utf-8") as handle:
        data = json.load(handle)
    result = composite(data)
    with open(argv[2], "w", encoding="utf-8") as handle:
        json.dump(result, handle, separators=(",", ":"))


if __name__ == "__main__":
    main()
