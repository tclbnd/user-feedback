"""RGBA PNG reader and APNG frame viewer.

Three states are kept strictly apart:

* compressed frame descriptions (parsed once, never a display canvas),
* the display canvas of the frame being produced,
* the canvas after that frame's disposal, which is the starting state of the
  next frame.

Random access keeps a small number of persistent post-disposal checkpoints
and replays only the frames between a checkpoint and the requested frame, so
frame pacing and the visible canvas stay identical to sequential playback.
"""
import argparse
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

DISPOSE_NONE = 0
DISPOSE_BACKGROUND = 1
DISPOSE_PREVIOUS = 2

BLEND_SOURCE = 0
BLEND_OVER = 1

# Frames decoded per pass are remembered in a small bounded cache so a random
# walk does not re-inflate the same compressed rectangles repeatedly. The
# cache holds decoded rectangles only, never full-size canvases.
DECODE_CACHE = 32

# Canvas residency instrumentation. Composition always writes into a working
# canvas that is separate from saved checkpoint canvases, so the number of
# full-size canvases alive at any moment is bounded by the checkpoint budget
# plus a small constant. The tracker is cheap and stays enabled.
TRACK_CANVASES = True
_live_canvases = 0
_peak_canvases = 0


class Canvas(bytearray):
    """Full-size RGBA canvas, accounted for residency tests."""

    __slots__ = ("_tracked",)

    def __new__(cls, source):
        global _live_canvases, _peak_canvases
        canvas = super().__new__(cls, source)
        canvas._tracked = TRACK_CANVASES
        if TRACK_CANVASES:
            _live_canvases += 1
            if _live_canvases > _peak_canvases:
                _peak_canvases = _live_canvases
        return canvas

    def __del__(self):
        global _live_canvases
        if getattr(self, "_tracked", False):
            _live_canvases -= 1


def new_canvas(width, height, source=None):
    """Allocate a transparent canvas, or a copy of ``source``."""
    data = bytes(width * height * 4) if source is None else source
    return Canvas(data)


def reset_canvas_stats():
    global _live_canvases, _peak_canvases
    _live_canvases = 0
    _peak_canvases = 0


def canvas_stats():
    return {"live": _live_canvases, "peak": _peak_canvases}


def chunks(data):
    """Yield validated ``(kind, body, offset)`` PNG chunks.

    Damaged chunk boundaries, CRC mismatches and a missing IEND all mean the
    file cannot be trusted, so they are reported as truncated input.
    """
    if not data.startswith(SIGNATURE):
        raise ValueError("png_signature")
    cursor = len(SIGNATURE)
    while cursor < len(data):
        if cursor + 12 > len(data):
            raise ValueError(f"truncated: chunk header at {cursor}")
        length, kind = struct.unpack_from(">I4s", data, cursor)
        start = cursor + 8
        end = start + length
        if end + 4 > len(data):
            raise ValueError(f"truncated: chunk {kind!r} at {cursor}")
        body = data[start:end]
        crc, = struct.unpack_from(">I", data, end)
        if zlib.crc32(kind + body) != crc:
            # A chunk whose payload no longer matches its CRC is unusable
            # frame data, so it belongs to the truncated-input class.
            raise ValueError(
                f"truncated: crc mismatch in {kind!r} at {cursor}")
        yield kind, body, cursor
        cursor = end + 4
        if kind == b"IEND":
            return
    raise ValueError("truncated: missing IEND")


def pixels(width, height, compressed):
    """Decode one RGBA8, non-interlaced frame (reusing the filter path)."""
    try:
        data = zlib.decompress(compressed)
    except zlib.error as error:
        raise ValueError(f"truncated: zlib stream ({error})") from None
    stride = 4 * width
    if len(data) != (stride + 1) * height:
        raise ValueError("truncated: scanline size")
    last = bytearray(stride)
    result = bytearray()
    for y in range(height):
        pos = y * (stride + 1)
        mode = data[pos]
        if mode > 4:
            raise ValueError("scanline_filter")
        row = bytearray(data[pos + 1:pos + stride + 1])
        if mode == 0:
            result.extend(row)
            last = row
            continue
        for j in range(stride):
            a = row[j - 4] if j >= 4 else 0
            b = last[j]
            c = last[j - 4] if j >= 4 else 0
            if mode == 1:
                v = a
            elif mode == 2:
                v = b
            elif mode == 3:
                v = (a + b) // 2
            elif mode == 4:
                p = a + b - c
                v = min((a, b, c), key=lambda k: abs(p - k))
            else:
                v = 0
            row[j] = (row[j] + v) % 256
        result.extend(row)
        last = row
    return bytes(result)


class Frame:
    """Decoded frame description, independent of any canvas."""

    __slots__ = ("width", "height", "x", "y", "delay_num", "delay_den",
                 "dispose", "blend", "data")

    def __init__(self, width, height, x, y, delay_num, delay_den,
                 dispose, blend, data):
        self.width = width
        self.height = height
        self.x = x
        self.y = y
        self.delay_num = delay_num
        self.delay_den = delay_den
        self.dispose = dispose
        self.blend = blend
        self.data = data

    def duration(self):
        return Fraction(self.delay_num, self.delay_den or 100)


class Image:
    __slots__ = ("width", "height", "frames", "static_rgba", "animated")

    def __init__(self, width, height, frames, static_rgba, animated):
        self.width = width
        self.height = height
        self.frames = frames
        self.static_rgba = static_rgba
        self.animated = animated


def _check_rect(width, height, x, y, canvas_width, canvas_height, offset):
    if width == 0 or height == 0:
        raise ValueError(f"rectangle: empty frame at {offset}")
    if width > canvas_width or height > canvas_height \
            or x + width > canvas_width or y + height > canvas_height:
        raise ValueError(
            f"rectangle: frame at {offset} "
            f"({x},{y} {width}x{height}) outside {canvas_width}x{canvas_height}")


def _unpack_fctl(body, offset, canvas_width, canvas_height):
    if len(body) != 26:
        raise ValueError(f"truncated: fcTL length at {offset}")
    seq, fw, fh, fx, fy, dn, dd, dispose, blend = \
        struct.unpack(">IIIIIHHBB", body)
    _check_rect(fw, fh, fx, fy, canvas_width, canvas_height, offset)
    if dispose > DISPOSE_PREVIOUS:
        raise ValueError(f"sequence: invalid dispose_op at {offset}")
    if blend > BLEND_OVER:
        raise ValueError(f"sequence: invalid blend_op at {offset}")
    return seq, Frame(fw, fh, fx, fy, dn, dd, dispose, blend, b"")


def parse(data):
    """Parse a PNG/APNG into frame descriptions plus an optional still image."""
    items = list(chunks(data))
    if not items:
        raise ValueError("truncated: empty file")
    kind, body, offset = items[0]
    if kind != b"IHDR":
        raise ValueError(f"truncated: missing IHDR (found {kind!r})")
    if len(body) != 13:
        raise ValueError("truncated: IHDR length")
    width, height, depth, colour, compression, filtering, interlace = \
        struct.unpack(">IIBBBBB", body)
    if width == 0 or height == 0:
        raise ValueError("rectangle: zero-sized canvas")
    if depth != 8 or colour != 6 or compression != 0 or filtering != 0 \
            or interlace != 0:
        raise ValueError("unsupported_image: expected non-interlaced RGBA8")

    actl = None
    frames = []
    static_parts = []
    current = None
    current_parts = None
    current_uses_idat = False
    seen_idat = False
    seen_fdat = False
    seen_fctl = False
    expected_seq = 0

    def finish_current():
        nonlocal current, current_parts
        if current is None:
            return
        if not current_parts:
            raise ValueError(
                f"truncated: no data for animation frame {len(frames)}")
        current.data = b"".join(current_parts)
        frames.append(current)
        current = None
        current_parts = None

    for kind, body, offset in items[1:]:
        if kind == b"IHDR":
            raise ValueError(f"sequence: multiple IHDR at {offset}")
        if kind == b"acTL":
            if actl is not None:
                raise ValueError(f"sequence: multiple acTL at {offset}")
            if seen_idat:
                raise ValueError(f"sequence: acTL after IDAT at {offset}")
            if seen_fctl:
                raise ValueError(f"sequence: acTL after fcTL at {offset}")
            if len(body) != 8:
                raise ValueError(f"truncated: acTL length at {offset}")
            num_frames, num_plays = struct.unpack(">II", body)
            if num_frames == 0:
                raise ValueError(f"sequence: acTL declares no frames at {offset}")
            actl = (num_frames, num_plays)
            continue
        if kind == b"fcTL":
            if actl is None:
                raise ValueError(f"sequence: fcTL without acTL at {offset}")
            seq, frame = _unpack_fctl(body, offset, width, height)
            if seq != expected_seq:
                raise ValueError(
                    f"sequence: expected {expected_seq}, got {seq} at {offset}")
            expected_seq += 1
            finish_current()
            current = frame
            current_parts = []
            # Only the fcTL that precedes the first IDAT owns that IDAT.
            current_uses_idat = not seen_idat
            seen_fctl = True
            continue
        if kind == b"IDAT":
            if actl is None:
                static_parts.append(body)
            elif seen_fdat:
                raise ValueError(f"sequence: IDAT after fdAT at {offset}")
            elif current is not None:
                if not current_uses_idat:
                    raise ValueError(f"sequence: IDAT after fdAT frame at {offset}")
                current_parts.append(body)
            elif seen_fctl:
                raise ValueError(f"sequence: unexpected IDAT at {offset}")
            else:
                # Default image stored separately from the animation.
                static_parts.append(body)
            seen_idat = True
            continue
        if kind == b"fdAT":
            if actl is None:
                raise ValueError(f"sequence: fdAT without acTL at {offset}")
            if len(body) < 4:
                raise ValueError(f"truncated: fdAT length at {offset}")
            seq, = struct.unpack(">I", body[:4])
            if seq != expected_seq:
                raise ValueError(
                    f"sequence: expected {expected_seq}, got {seq} at {offset}")
            expected_seq += 1
            if not seen_idat:
                raise ValueError(f"sequence: fdAT before IDAT at {offset}")
            if current is None or current_uses_idat:
                raise ValueError(f"sequence: fdAT without fcTL at {offset}")
            current_parts.append(body[4:])
            seen_fdat = True
            continue
        if kind == b"IEND":
            if len(body) != 0:
                raise ValueError(f"truncated: IEND length at {offset}")
            break
        # Ancillary chunks do not affect canvas composition.

    finish_current()

    if actl is None:
        if not static_parts:
            raise ValueError("truncated: missing IDAT")
        static_rgba = pixels(width, height, b"".join(static_parts))
        return Image(width, height, [], static_rgba, False)

    if not seen_idat:
        raise ValueError("truncated: missing IDAT")
    if not frames:
        raise ValueError("truncated: no animation frames")
    num_frames, _num_plays = actl
    if num_frames != len(frames):
        raise ValueError(
            f"sequence: acTL declares {num_frames} frames, found {len(frames)}")
    static_rgba = None
    if static_parts:
        # The default image is not part of the animation, but it must still be
        # a complete image (this also validates its zlib stream).
        static_rgba = pixels(width, height, b"".join(static_parts))
    return Image(width, height, frames, static_rgba, True)


def round_div(numerator, denominator):
    """Round a non-negative fraction to nearest integer, halves upward."""
    return (2 * numerator + denominator) // (2 * denominator)


def compose(display, canvas_width, frame, raw):
    """Draw one decoded frame into the working canvas in place.

    SOURCE replaces the rectangle outright. OVER uses the exact
    non-premultiplied source-over formula with one rounding step per channel,
    halves rounded upward; vanishing source (alpha 0) keeps the destination.
    """
    row_bytes = frame.width * 4
    if frame.blend == BLEND_SOURCE:
        for fy in range(frame.height):
            src = fy * row_bytes
            dst = ((frame.y + fy) * canvas_width + frame.x) * 4
            display[dst:dst + row_bytes] = raw[src:src + row_bytes]
        return
    for fy in range(frame.height):
        src = fy * row_bytes
        dst = ((frame.y + fy) * canvas_width + frame.x) * 4
        for _fx in range(frame.width):
            sa = raw[src + 3]
            if sa == 255:
                display[dst:dst + 4] = raw[src:src + 4]
            elif sa:
                da = display[dst + 3]
                if da == 0:
                    # Over a transparent pixel the exact formula reduces to
                    # the source pixel itself.
                    display[dst:dst + 4] = raw[src:src + 4]
                else:
                    da_part = da * (255 - sa)
                    den = sa * 255 + da_part
                    display[dst] = round_div(
                        raw[src] * sa * 255 + display[dst] * da_part, den)
                    display[dst + 1] = round_div(
                        raw[src + 1] * sa * 255 + display[dst + 1] * da_part,
                        den)
                    display[dst + 2] = round_div(
                        raw[src + 2] * sa * 255 + display[dst + 2] * da_part,
                        den)
                    display[dst + 3] = round_div(den, 255)
            src += 4
            dst += 4


def clear_rect(canvas, canvas_width, frame):
    """BACKGROUND disposal: the frame rectangle becomes (0, 0, 0, 0)."""
    row_bytes = frame.width * 4
    blank = bytes(row_bytes)
    for y in range(frame.height):
        start = ((frame.y + y) * canvas_width + frame.x) * 4
        canvas[start:start + row_bytes] = blank


def snapshot_rect(canvas, canvas_width, frame):
    """Copy the region a frame covers, for PREVIOUS disposal."""
    row_bytes = frame.width * 4
    saved = bytearray(row_bytes * frame.height)
    for y in range(frame.height):
        start = ((frame.y + y) * canvas_width + frame.x) * 4
        saved[y * row_bytes:(y + 1) * row_bytes] = \
            canvas[start:start + row_bytes]
    return saved


def restore_rect(canvas, canvas_width, frame, saved):
    """PREVIOUS disposal: restore the saved region."""
    row_bytes = frame.width * 4
    for y in range(frame.height):
        start = ((frame.y + y) * canvas_width + frame.x) * 4
        canvas[start:start + row_bytes] = saved[y * row_bytes:(y + 1) * row_bytes]


def effective_dispose(index, frame):
    """First-frame rule: PREVIOUS on frame 0 behaves as BACKGROUND."""
    if index == 0 and frame.dispose == DISPOSE_PREVIOUS:
        return DISPOSE_BACKGROUND
    return frame.dispose


def checkpoint_indices(frame_count, budget):
    """Post-disposal checkpoints, evenly spaced, never after the last frame."""
    count = min(max(budget, 0), max(frame_count - 1, 0))
    if count == 0:
        return []
    segments = count + 1
    return [((k * frame_count + segments - 1) // segments) - 1
            for k in range(1, count + 1)]


def build_checkpoints(image, indices):
    """One sequential pass; only a budgeted number of canvases is retained.

    ``canvas`` always holds the post-disposal state, i.e. exactly the canvas
    the next frame starts from. It is never handed out, so it can be mutated
    in place while stepping and copied only when it becomes a checkpoint.
    """
    width, frames = image.width, image.frames
    wanted = set(indices)
    checkpoints = {}
    canvas = new_canvas(width, image.height)
    for index, frame in enumerate(frames):
        raw = pixels(frame.width, frame.height, frame.data)
        dispose = effective_dispose(index, frame)
        previous = snapshot_rect(canvas, width, frame) \
            if dispose == DISPOSE_PREVIOUS else None
        compose(canvas, width, frame, raw)
        if dispose == DISPOSE_BACKGROUND:
            clear_rect(canvas, width, frame)
        elif dispose == DISPOSE_PREVIOUS:
            restore_rect(canvas, width, frame, previous)
        if index in wanted:
            checkpoints[index] = new_canvas(width, image.height, canvas)
    return checkpoints


def replay_display(image, checkpoints, index, cache=None):
    """Replay the necessary interval and return the requested display canvas.

    The starting canvas is the closest post-disposal checkpoint before
    ``index`` (or the transparent initial canvas). Nothing is carried over
    between requests, so jumping, looping and reversing direction all rebuild
    the exact canvas sequential playback would have shown, and a frame with
    PREVIOUS disposal can never leak into a later one.

    ``cache`` is an optional bounded map from frame index to decoded frame
    pixels; it never holds a canvas, so it cannot duplicate display frames.
    """
    width, frames = image.width, image.frames

    def decoded(current):
        if cache is None:
            frame = frames[current]
            return pixels(frame.width, frame.height, frame.data)
        raw = cache.get(current)
        if raw is not None:
            return raw
        frame = frames[current]
        raw = pixels(frame.width, frame.height, frame.data)
        if len(cache) >= DECODE_CACHE:
            cache.pop(next(iter(cache)))
        cache[current] = raw
        return raw

    base_index, base = -1, None
    for cp_index in sorted(checkpoints):
        if cp_index >= index:
            break
        base_index, base = cp_index, checkpoints[cp_index]
    canvas = new_canvas(width, image.height, base)
    for current in range(base_index + 1, index):
        frame = frames[current]
        dispose = effective_dispose(current, frame)
        previous = snapshot_rect(canvas, width, frame) \
            if dispose == DISPOSE_PREVIOUS else None
        compose(canvas, width, frame, decoded(current))
        if dispose == DISPOSE_BACKGROUND:
            clear_rect(canvas, width, frame)
        elif dispose == DISPOSE_PREVIOUS:
            restore_rect(canvas, width, frame, previous)
    # The requested frame is drawn last and returned as the display state;
    # its own disposal is never applied because nothing follows it.
    compose(canvas, width, frames[index], decoded(index))
    return canvas, index - base_index


def fraction_pair(value):
    return [value.numerator, value.denominator]


def view(data, requests, budget):
    reset_canvas_stats()
    if requests is not None:
        if not isinstance(requests, list):
            raise ValueError("request: frames must be a list")
        for index in requests:
            _check_request(index)
    image = parse(data)
    if not image.animated:
        requested = [0] if requests is None else requests
        for index in requested:
            if index != 0:
                raise ValueError(f"request: frame {index} out of range")
        digest = hashlib.sha256(image.static_rgba).hexdigest()
        return {
            "frames": [{"index": 0, "start": [0, 1], "duration": [0, 1],
                        "rgba_sha256": digest} for _ in requested],
            "stats": {"max_checkpoints": 0,
                      "max_replay_frames": 1 if requested else 0},
        }

    frame_count = len(image.frames)
    requested = list(range(frame_count)) if requests is None else requests
    for index in requested:
        if index < 0 or index >= frame_count:
            raise ValueError(f"request: frame {index} out of range")

    starts = []
    elapsed = Fraction(0, 1)
    for frame in image.frames:
        starts.append(elapsed)
        elapsed += frame.duration()

    checkpoints = build_checkpoints(
        image, checkpoint_indices(frame_count, budget))

    max_replay = 0
    output = []
    decoded = {}
    for index in requested:
        rgba, replayed = replay_display(image, checkpoints, index, decoded)
        max_replay = max(max_replay, replayed)
        output.append({
            "index": index,
            "start": fraction_pair(starts[index]),
            "duration": fraction_pair(image.frames[index].duration()),
            "rgba_sha256": hashlib.sha256(rgba).hexdigest(),
        })
    return {
        "frames": output,
        "stats": {
            "max_checkpoints": len(checkpoints),
            "max_replay_frames": max_replay,
        },
    }


def _check_request(index):
    if not isinstance(index, int) or isinstance(index, bool):
        raise ValueError(f"request: non-integer frame index {index!r}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--budget", type=int, default=4)
    args = parser.parse_args()
    try:
        request = None
        if args.request:
            try:
                document = json.loads(args.request.read_text())
            except json.JSONDecodeError as error:
                raise ValueError(f"request: unreadable JSON ({error})") from None
            if "frames" not in document:
                raise ValueError("request: missing frames key")
            request = document["frames"]
        if args.budget < 0:
            raise ValueError(f"budget: negative budget {args.budget}")
        result = view(args.input.read_bytes(), request, args.budget)
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    except FileNotFoundError as error:
        parser.exit(2, f"input: {error}\n")
    except (ValueError, struct.error, zlib.error, KeyError, TypeError,
            OSError) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
