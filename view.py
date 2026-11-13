"""APNG frame viewer.

Decoding is split into three stages:

* frame decode: each fcTL rectangle's own pixel buffer (from IDAT/fdAT);
* display state: the transient canvas while a frame is on screen;
* post-disposal canvas: the persistent state after NONE/BACKGROUND/PREVIOUS
  disposal, which is what the next frame composes onto.

Random access is served from a bounded set of persistent post-disposal
checkpoints plus replay of the frames between the checkpoint and the target.
"""
import argparse
import hashlib
import json
from fractions import Fraction
from pathlib import Path
import struct
import zlib

SIGNATURE = b"\x89PNG\r\n\x1a\n"

DISPOSE_NONE = 0
DISPOSE_BACKGROUND = 1
DISPOSE_PREVIOUS = 2
BLEND_SOURCE = 0
BLEND_OVER = 1


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
        yield kind, body
        cursor = end + 4
        if kind == b"IEND":
            return
    raise ValueError("truncated: missing IEND")


def pixels(width, height, compressed):
    try:
        data = zlib.decompress(compressed)
    except zlib.error as error:
        raise ValueError(f"image_data: {error}")
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
    """One animation frame: its rectangle, timing, ops and compressed data."""
    __slots__ = ("x", "y", "w", "h", "delay_num", "delay_den",
                 "dispose", "blend", "data", "_pixels")

    def __init__(self, x, y, w, h, delay_num, delay_den, dispose, blend):
        self.x, self.y, self.w, self.h = x, y, w, h
        self.delay_num, self.delay_den = delay_num, delay_den
        self.dispose, self.blend = dispose, blend
        self.data = []
        self._pixels = None

    def decoded(self):
        if self._pixels is None:
            self._pixels = pixels(self.w, self.h, b"".join(self.data))
        return self._pixels

    @property
    def delay(self):
        return Fraction(self.delay_num, self.delay_den or 100)


def parse(data):
    """Parse PNG/APNG chunks into (width, height, frames), validating structure."""
    width = height = None
    actl = None
    frames = []
    idat = []
    idat_in_animation = False
    seen_idat = False
    expected_seq = 0
    first = True
    for kind, body in chunks(data):
        if first:
            if kind != b"IHDR":
                raise ValueError("ihdr: first chunk is " + repr(kind))
            first = False
        if kind == b"IHDR":
            if width is not None:
                raise ValueError("ihdr: duplicate")
            if len(body) != 13:
                raise ValueError("truncated ihdr")
            width, height, depth, color, comp, filt, inter = struct.unpack(">IIBBBBB", body)
            if width == 0 or height == 0:
                raise ValueError("ihdr: zero size")
            if (depth, color, comp, filt, inter) != (8, 6, 0, 0, 0):
                raise ValueError(f"unsupported ihdr depth={depth} color={color} "
                                 f"compression={comp} filter={filt} interlace={inter}")
        elif kind == b"acTL":
            if actl is not None:
                raise ValueError("actl: duplicate")
            if seen_idat or frames:
                raise ValueError("actl: must precede fcTL and IDAT")
            if len(body) != 8:
                raise ValueError("truncated actl")
            num_frames, num_plays = struct.unpack(">II", body)
            if num_frames == 0:
                raise ValueError("actl: zero frames")
            actl = (num_frames, num_plays)
        elif kind == b"fcTL":
            if actl is None:
                raise ValueError("actl: missing before fcTL")
            if len(body) != 26:
                raise ValueError("truncated fctl")
            (seq, w, h, x, y, delay_num, delay_den,
             dispose, blend) = struct.unpack(">IIIIIHHBB", body)
            if seq != expected_seq:
                raise ValueError(f"sequence: fcTL has {seq}, expected {expected_seq}")
            expected_seq += 1
            if w == 0 or h == 0 or x + w > width or y + h > height:
                raise ValueError(f"rectangle: {x},{y} {w}x{h} outside {width}x{height}")
            if dispose > 2 or blend > 1:
                raise ValueError(f"operation: dispose={dispose} blend={blend}")
            frames.append(Frame(x, y, w, h, delay_num, delay_den, dispose, blend))
            if not seen_idat and len(frames) == 1:
                idat_in_animation = True
        elif kind == b"IDAT":
            seen_idat = True
            if idat_in_animation:
                frames[0].data.append(body)
            else:
                idat.append(body)
        elif kind == b"fdAT":
            if actl is None or not frames:
                raise ValueError("fdat: chunk without preceding fcTL")
            if idat_in_animation and len(frames) == 1:
                raise ValueError("fdat: first frame must use IDAT")
            if len(body) < 4:
                raise ValueError("truncated fdat")
            seq, = struct.unpack_from(">I", body)
            if seq != expected_seq:
                raise ValueError(f"sequence: fdAT has {seq}, expected {expected_seq}")
            expected_seq += 1
            frames[-1].data.append(body[4:])
    if width is None:
        raise ValueError("ihdr: missing")
    if actl is not None:
        if len(frames) != actl[0]:
            raise ValueError(f"actl: declares {actl[0]} frames, found {len(frames)}")
        for i, frame in enumerate(frames):
            if not frame.data:
                raise ValueError(f"missing image data for frame {i}")
        return width, height, frames
    if not idat:
        raise ValueError("missing IDAT")
    still = Frame(0, 0, width, height, 0, 1, DISPOSE_NONE, BLEND_SOURCE)
    still.data = list(idat)
    return width, height, [still]


def compose(canvas, canvas_width, frame, source, is_first):
    """Composite the frame rectangle onto the canvas (the display state)."""
    blend = frame.blend
    if is_first and blend == BLEND_OVER:
        blend = BLEND_SOURCE  # first-frame rule
    row_bytes = frame.w * 4
    for row in range(frame.h):
        dst = ((frame.y + row) * canvas_width + frame.x) * 4
        src = row * row_bytes
        if blend == BLEND_SOURCE:
            canvas[dst:dst + row_bytes] = source[src:src + row_bytes]
            continue
        for col in range(frame.w):
            di = dst + col * 4
            si = src + col * 4
            sa = source[si + 3]
            if sa == 0:
                continue  # fully transparent source keeps the destination
            if sa == 255:
                canvas[di:di + 4] = source[si:si + 4]
                continue
            da = canvas[di + 3]
            alpha = sa * 255 + da * (255 - sa)
            for k in range(3):
                num = source[si + k] * sa * 255 + canvas[di + k] * da * (255 - sa)
                canvas[di + k] = (2 * num + alpha) // (2 * alpha)
            canvas[di + 3] = (2 * alpha + 255) // 510


def region_copy(canvas, canvas_width, frame):
    out = bytearray()
    row_bytes = frame.w * 4
    for row in range(frame.h):
        dst = ((frame.y + row) * canvas_width + frame.x) * 4
        out += canvas[dst:dst + row_bytes]
    return bytes(out)


def region_clear(canvas, canvas_width, frame):
    zero = b"\0" * (frame.w * 4)
    for row in range(frame.h):
        dst = ((frame.y + row) * canvas_width + frame.x) * 4
        canvas[dst:dst + len(zero)] = zero


def region_restore(canvas, canvas_width, frame, saved):
    row_bytes = frame.w * 4
    for row in range(frame.h):
        dst = ((frame.y + row) * canvas_width + frame.x) * 4
        src = row * row_bytes
        canvas[dst:dst + row_bytes] = saved[src:src + row_bytes]


def apply_frame(canvas, canvas_width, frame, source, is_first):
    """Compose a frame and then apply its disposal, yielding the post-disposal
    canvas.  Disposal order: save (PREVIOUS) -> compose -> clear/restore."""
    dispose = frame.dispose
    if is_first and dispose == DISPOSE_PREVIOUS:
        dispose = DISPOSE_BACKGROUND  # first-frame rule: nothing to restore
    saved = region_copy(canvas, canvas_width, frame) if dispose == DISPOSE_PREVIOUS else None
    compose(canvas, canvas_width, frame, source, is_first)
    if dispose == DISPOSE_BACKGROUND:
        region_clear(canvas, canvas_width, frame)
    elif dispose == DISPOSE_PREVIOUS:
        region_restore(canvas, canvas_width, frame, saved)


class Player:
    """Serves display frames from bounded post-disposal checkpoints.

    The transparent initial canvas is implicit and never stored.  Only the
    checkpoint canvases persist; transient display states and PREVIOUS
    save-buffers are temporary and do not count against the budget.
    """

    def __init__(self, width, height, frames, budget):
        self.width = width
        self.frames = frames
        self.size = width * height * 4
        count = len(frames)
        positions = []
        if budget > 0 and count > 1:
            step = -(-count // (budget + 1))
            pos = step - 1
            while pos < count - 1 and len(positions) < budget:
                positions.append(pos)
                pos += step
        wanted = set(positions)
        self.checkpoints = {}
        canvas = bytearray(self.size)
        for i, frame in enumerate(frames):
            apply_frame(canvas, width, frame, frame.decoded(), i == 0)
            if i in wanted:
                self.checkpoints[i] = bytes(canvas)
        self.max_checkpoints = len(self.checkpoints)
        self.max_replay = 0

    def render(self, index):
        """Return (display RGBA bytes, replayed frame count) for one frame."""
        base = -1
        for point in self.checkpoints:
            if base < point < index:
                base = point
        if base < 0:
            canvas = bytearray(self.size)  # implicit transparent initial state
            start = 0
        else:
            canvas = bytearray(self.checkpoints[base])
            start = base + 1
        for j in range(start, index):
            frame = self.frames[j]
            apply_frame(canvas, self.width, frame, frame.decoded(), j == 0)
        frame = self.frames[index]
        compose(canvas, self.width, frame, frame.decoded(), index == 0)
        replay = index - start + 1
        if replay > self.max_replay:
            self.max_replay = replay
        return bytes(canvas), replay


def view(data, requests, budget):
    if budget < 0:
        raise ValueError(f"budget: {budget} is negative")
    width, height, frames = parse(data)
    player = Player(width, height, frames, budget)
    delays = [frame.delay for frame in frames]
    starts = []
    moment = Fraction(0)
    for delay in delays:
        starts.append(moment)
        moment += delay
    count = len(frames)
    requested = list(range(count)) if requests is None else list(requests)
    output = []
    for index in requested:
        if isinstance(index, bool) or not isinstance(index, int) or not 0 <= index < count:
            raise ValueError(f"index: {index!r} outside 0..{count - 1}")
        display, _ = player.render(index)
        output.append({
            "index": index,
            "start": [starts[index].numerator, starts[index].denominator],
            "duration": [delays[index].numerator, delays[index].denominator],
            "rgba_sha256": hashlib.sha256(display).hexdigest(),
        })
    return {"frames": output,
            "stats": {"max_checkpoints": player.max_checkpoints,
                      "max_replay_frames": player.max_replay}}


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
            spec = json.loads(args.request.read_text())
            if not isinstance(spec, dict) or not isinstance(spec.get("frames"), list):
                raise ValueError("request: expected object with frames list")
            request = spec["frames"]
        result = view(args.input.read_bytes(), request, args.budget)
        args.output.write_text(json.dumps(result, sort_keys=True) + "\n")
    except (ValueError, struct.error, zlib.error) as error:
        parser.exit(2, str(error) + "\n")


if __name__ == "__main__":
    main()
