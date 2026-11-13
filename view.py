"""RGBA PNG/APNG frame viewer.

Splits three concerns: decoded frame rectangles (source data), the
temporary display state produced by SOURCE/OVER compositing, and the
persistent post-disposal canvas. Random access is served from a bounded
set of post-disposal checkpoints plus replay of the remaining interval,
so seeking, looping and direction reversal all observe identical frame
timing and final display images.
"""
import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

NONE, BACKGROUND, PREVIOUS = 0, 1, 2
SOURCE, OVER = 0, 1


def chunks(data):
    if not data.startswith(SIGNATURE):
        raise ValueError("png_signature")
    cursor = len(SIGNATURE)
    while cursor < len(data):
        if cursor + 12 > len(data):
            raise ValueError(f"truncated at {cursor}")
        length, kind = struct.unpack_from(">I4s", data, cursor)
        start = cursor + 8
        end = start + length
        if end + 4 > len(data):
            raise ValueError(f"truncated at {cursor}")
        body = data[start:end]
        crc, = struct.unpack_from(">I", data, end)
        if zlib.crc32(kind + body) != crc:
            raise ValueError(f"crc at {cursor}")
        yield kind, body, cursor
        cursor = end + 4
        if kind == b"IEND":
            return
    raise ValueError("truncated: missing IEND")


def pixels(width, height, compressed):
    data = zlib.decompress(compressed)
    stride = 4 * width
    if len(data) != (stride + 1) * height:
        raise ValueError("scanline_size")
    last = bytearray(stride)
    result = bytearray()
    for y in range(height):
        pos = y * (stride + 1)
        mode = data[pos]
        if mode > 4:
            raise ValueError("scanline_filter")
        row = bytearray(data[pos + 1:pos + stride + 1])
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
    """One animation frame: decoded source rectangle plus timing."""
    __slots__ = ("width", "height", "x", "y", "delay_num", "delay_den",
                 "dispose", "blend", "data", "default", "rgba")

    def __init__(self, width, height, x, y, delay_num, delay_den, dispose, blend):
        self.width = width
        self.height = height
        self.x = x
        self.y = y
        self.delay_num = delay_num
        self.delay_den = delay_den
        self.dispose = dispose
        self.blend = blend
        self.data = []
        self.default = False
        self.rgba = None


def parse(data):
    """Validate chunk structure and return (width, height, frames)."""
    width = height = None
    ihdr_seen = False
    actl = None
    frames = []
    pending = None
    saw_idat = False
    saw_fdat = False
    default_idat = []
    expected = 0
    for kind, body, offset in chunks(data):
        if not ihdr_seen:
            if kind != b"IHDR":
                raise ValueError(f"ihdr: missing before chunk at {offset}")
            if len(body) != 13:
                raise ValueError(f"ihdr: bad length {len(body)} at {offset}")
            width, height, depth, color, comp, filt, inter = struct.unpack(">IIBBBBB", body)
            if width == 0 or height == 0:
                raise ValueError(f"ihdr: zero canvas at {offset}")
            if (depth, color, comp, filt, inter) != (8, 6, 0, 0, 0):
                raise ValueError(f"ihdr: need 8-bit RGBA non-interlaced at {offset}")
            ihdr_seen = True
            continue
        if kind == b"IHDR":
            raise ValueError(f"ihdr: duplicate at {offset}")
        if kind == b"acTL":
            if actl is not None:
                raise ValueError(f"actl: duplicate at {offset}")
            if saw_idat or frames:
                raise ValueError(f"actl: must precede image data at {offset}")
            if len(body) != 8:
                raise ValueError(f"actl: bad length {len(body)} at {offset}")
            num_frames, _num_plays = struct.unpack(">II", body)
            if num_frames == 0:
                raise ValueError(f"actl: zero frames at {offset}")
            actl = num_frames
        elif kind == b"fcTL":
            if actl is None:
                raise ValueError(f"actl: fcTL without acTL at {offset}")
            if len(body) != 26:
                raise ValueError(f"fctl: bad length {len(body)} at {offset}")
            seq, w, h, x, y, dn, dd, dispose, blend = struct.unpack(">IIIIIHHBB", body)
            if seq != expected:
                raise ValueError(f"sequence: expected {expected} got {seq} at {offset}")
            expected += 1
            if w == 0 or h == 0 or x + w > width or y + h > height:
                raise ValueError(
                    f"rectangle: frame {len(frames)} rect {x},{y} {w}x{h} "
                    f"exceeds {width}x{height} at {offset}")
            if dispose > PREVIOUS:
                raise ValueError(f"fctl: dispose_op {dispose} at {offset}")
            if blend > OVER:
                raise ValueError(f"fctl: blend_op {blend} at {offset}")
            if pending is not None and not pending.data:
                raise ValueError(
                    f"sequence: frame {len(frames) - 1} has no data chunks at {offset}")
            pending = Frame(w, h, x, y, dn, dd, dispose, blend)
            pending.default = not saw_idat
            frames.append(pending)
        elif kind == b"IDAT":
            saw_idat = True
            if pending is not None and pending.default:
                if saw_fdat:
                    raise ValueError(f"sequence: IDAT after fdAT at {offset}")
                pending.data.append(body)
            elif pending is None:
                default_idat.append(body)
            else:
                raise ValueError(f"sequence: stray IDAT at {offset}")
        elif kind == b"fdAT":
            if actl is None:
                raise ValueError(f"actl: fdAT without acTL at {offset}")
            if len(body) < 4:
                raise ValueError(f"truncated: fdAT body {len(body)} at {offset}")
            seq, = struct.unpack_from(">I", body)
            if seq != expected:
                raise ValueError(f"sequence: expected {expected} got {seq} at {offset}")
            expected += 1
            if pending is None or pending.default:
                raise ValueError(f"sequence: fdAT without frame at {offset}")
            saw_fdat = True
            pending.data.append(body[4:])
        elif kind == b"IEND":
            break
    if not ihdr_seen:
        raise ValueError("ihdr: missing")
    if not saw_idat:
        raise ValueError("idat: missing")
    if pending is not None and not pending.data:
        raise ValueError(f"sequence: frame {len(frames) - 1} has no data chunks")
    if actl is None:
        frame = Frame(width, height, 0, 0, 0, 0, NONE, SOURCE)
        frame.data = default_idat
        frames = [frame]
    elif len(frames) != actl:
        raise ValueError(f"actl: num_frames {actl} but parsed {len(frames)}")
    return width, height, frames


def decode(frame, index):
    if frame.rgba is None:
        try:
            frame.rgba = pixels(frame.width, frame.height, b"".join(frame.data))
        except zlib.error as error:
            raise ValueError(f"inflate: frame {index}: {error}")
        except ValueError as error:
            raise ValueError(f"frame {index}: {error}")
    return frame.rgba


class Player:
    """Renders display frames on demand.

    Persistent state is limited to post-disposal checkpoint canvases
    (at most `budget`); the transparent initial state is implicit and
    temporary display/restore buffers are never retained. A request is
    served by restoring the newest checkpoint before the target frame
    and replaying the remaining interval.
    """

    def __init__(self, width, height, frames, budget):
        self.width = width
        self.height = height
        self.frames = frames
        self.canvas_size = width * height * 4
        count = len(frames)
        self.spacing = max(1, -(-count // budget)) if budget >= 1 else count + 1
        self.checkpoints = {}
        self.durations = [Fraction(f.delay_num, f.delay_den or 100) for f in frames]
        self.starts = []
        moment = Fraction(0)
        for duration in self.durations:
            self.starts.append(moment)
            moment += duration
        canvas = bytearray(self.canvas_size)
        for index in range(count):
            self.step(canvas, index)
            if (index + 1) % self.spacing == 0 and index + 1 < count:
                self.checkpoints[index] = bytes(canvas)
        self.max_checkpoints = len(self.checkpoints)

    def step(self, canvas, index):
        """Composite frame `index` onto a display copy of `canvas`, then
        apply its disposal to `canvas`. Returns the display bytes."""
        frame = self.frames[index]
        dispose = frame.dispose
        if index == 0 and dispose == PREVIOUS:
            dispose = BACKGROUND
        saved = bytes(canvas) if dispose == PREVIOUS else None
        display = bytearray(canvas)
        self.composite(display, frame, index)
        if dispose == NONE:
            canvas[:] = display
        elif dispose == BACKGROUND:
            canvas[:] = display
            self.clear(canvas, frame)
        else:
            canvas[:] = saved
        return bytes(display)

    def composite(self, display, frame, index):
        src = decode(frame, index)
        row_bytes = frame.width * 4
        for row in range(frame.height):
            src_pos = row * row_bytes
            dst_pos = ((frame.y + row) * self.width + frame.x) * 4
            if frame.blend == SOURCE:
                display[dst_pos:dst_pos + row_bytes] = src[src_pos:src_pos + row_bytes]
                continue
            for col in range(frame.width):
                sp = src_pos + col * 4
                alpha = src[sp + 3]
                dp = dst_pos + col * 4
                if alpha == 0:
                    continue
                if alpha == 255:
                    display[dp:dp + 4] = src[sp:sp + 4]
                    continue
                da = display[dp + 3]
                den = alpha * 255 + da * (255 - alpha)
                for channel in range(3):
                    num = (src[sp + channel] * alpha * 255
                           + display[dp + channel] * da * (255 - alpha))
                    display[dp + channel] = (2 * num + den) // (2 * den)
                display[dp + 3] = (2 * den + 255) // 510

    def clear(self, canvas, frame):
        row_bytes = frame.width * 4
        for row in range(frame.height):
            pos = ((frame.y + row) * self.width + frame.x) * 4
            canvas[pos:pos + row_bytes] = b"\0" * row_bytes

    def render_at(self, index):
        """Return (display_bytes, replayed_frame_count) for `index`."""
        base = max((c for c in self.checkpoints if c < index), default=None)
        if base is None:
            canvas = bytearray(self.canvas_size)
            first = 0
        else:
            canvas = bytearray(self.checkpoints[base])
            first = base + 1
        display = None
        for current in range(first, index + 1):
            display = self.step(canvas, current)
        return display, index - first + 1


def pair(value):
    return [value.numerator, value.denominator]


def view(data, requests, budget):
    width, height, frames = parse(data)
    player = Player(width, height, frames, budget)
    count = len(frames)
    requested = list(range(count)) if requests is None else list(requests)
    result = []
    max_replay = 0
    for index in requested:
        if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < count:
            raise ValueError(f"request: index {index!r} outside 0..{count - 1}")
        display, replayed = player.render_at(index)
        max_replay = max(max_replay, replayed)
        result.append({
            "index": index,
            "start": pair(player.starts[index]),
            "duration": pair(player.durations[index]),
            "rgba_sha256": hashlib.sha256(display).hexdigest(),
        })
    return {"frames": result,
            "stats": {"max_checkpoints": player.max_checkpoints,
                      "max_replay_frames": max_replay}}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--request", type=Path)
    parser.add_argument("--budget", type=int, default=4)
    args = parser.parse_args()
    try:
        request = json.loads(args.request.read_text())["frames"] if args.request else None
        result = view(args.input.read_bytes(), request, args.budget)
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    except (ValueError, struct.error, zlib.error) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
