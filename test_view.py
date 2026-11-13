"""Acceptance tests for view.py.

Covers: 120-frame fixture with consecutive PREVIOUS/BACKGROUND on the
same pixel checked frame-by-frame against an independent sequential
compositor, 400 seeded shuffled seeks, checkpoint budget/residency and
replay-count constraints, and locatable error codes for out-of-bounds
rectangles, duplicate sequence numbers and truncated data.
"""
import hashlib
import json
import random
import struct
import subprocess
import sys
import tempfile
from fractions import Fraction
from pathlib import Path
import zlib

import view

NONE, BACKGROUND, PREVIOUS = 0, 1, 2
SOURCE, OVER = 0, 1
SIGNATURE = b"\x89PNG\r\n\x1a\n"
HERE = Path(__file__).resolve().parent


def chunk(kind, data):
    return (struct.pack(">I", len(data)) + kind + data
            + struct.pack(">I", zlib.crc32(kind + data)))


def round_half_up(num, den):
    return (2 * num + den) // (2 * den)


def blend_pixel(src, dst):
    sr, sg, sb, sa = src
    if sa == 0:
        return dst
    dr, dg, db, da = dst
    den = sa * 255 + da * (255 - sa)
    return (
        round_half_up(sr * sa * 255 + dr * da * (255 - sa), den),
        round_half_up(sg * sa * 255 + dg * da * (255 - sa), den),
        round_half_up(sb * sa * 255 + db * da * (255 - sa), den),
        round_half_up(den, 255),
    )


def baseline(frames, width, height):
    """Independent sequential compositor: no checkpoints, no replay."""
    canvas = bytearray(width * height * 4)
    hashes = []
    for index, fr in enumerate(frames):
        dispose = fr["dispose"]
        if index == 0 and dispose == PREVIOUS:
            dispose = BACKGROUND
        saved = bytes(canvas) if dispose == PREVIOUS else None
        display = bytearray(canvas)
        pix = fr["pixels"]
        for row in range(fr["h"]):
            for col in range(fr["w"]):
                sp = (row * fr["w"] + col) * 4
                dp = ((fr["y"] + row) * width + fr["x"] + col) * 4
                if fr["blend"] == SOURCE:
                    display[dp:dp + 4] = pix[sp:sp + 4]
                else:
                    display[dp:dp + 4] = bytes(blend_pixel(
                        tuple(pix[sp:sp + 4]), tuple(display[dp:dp + 4])))
        hashes.append(hashlib.sha256(bytes(display)).hexdigest())
        if dispose == NONE:
            canvas[:] = display
        elif dispose == BACKGROUND:
            canvas[:] = display
            for row in range(fr["h"]):
                pos = ((fr["y"] + row) * width + fr["x"]) * 4
                canvas[pos:pos + fr["w"] * 4] = b"\0" * (fr["w"] * 4)
        else:
            canvas[:] = saved
    return hashes


def expected_times(frames):
    starts, durations = [], []
    moment = Fraction(0)
    for fr in frames:
        duration = Fraction(fr["dn"], fr["dd"] or 100)
        starts.append(moment)
        durations.append(duration)
        moment += duration
    return starts, durations


def pair(value):
    return [value.numerator, value.denominator]


def scanlines(pixels_bytes, w, h):
    return b"".join(b"\0" + pixels_bytes[r * w * 4:(r + 1) * w * 4]
                    for r in range(h))


def build_fixture(n=120, seed=7, width=16, height=16):
    """120-frame APNG; frames 40..59 hammer pixel (5,5) with alternating
    PREVIOUS/BACKGROUND 1x1 OVER updates. Default image is separate."""
    rng = random.Random(seed)
    frames = []
    for i in range(n):
        if 40 <= i < 60:
            fx, fy, fw, fh = 5, 5, 1, 1
            dispose = PREVIOUS if i % 2 == 0 else BACKGROUND
            blend = OVER
        elif i % 30 == 0:
            fx, fy, fw, fh = 0, 0, width, height
            dispose, blend = NONE, SOURCE
        else:
            fw = rng.randint(1, 8)
            fh = rng.randint(1, 8)
            fx = rng.randint(0, width - fw)
            fy = rng.randint(0, height - fh)
            dispose = rng.choice([NONE, BACKGROUND, PREVIOUS])
            blend = rng.choice([SOURCE, OVER])
        dn = rng.randint(1, 5)
        dd = rng.choice([0, 10, 25, 100])
        pix = bytearray()
        for _ in range(fw * fh):
            pix += bytes((rng.randrange(256), rng.randrange(256),
                          rng.randrange(256),
                          rng.choice([0, 64, 128, 200, 255])))
        frames.append({"x": fx, "y": fy, "w": fw, "h": fh, "dispose": dispose,
                       "blend": blend, "dn": dn, "dd": dd, "pixels": bytes(pix)})
    png = bytearray(SIGNATURE)
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
    png += chunk(b"acTL", struct.pack(">II", n, 0))
    default_raw = b"".join(b"\0" + bytes([9, 9, 9, 255]) * width
                           for _ in range(height))
    png += chunk(b"IDAT", zlib.compress(default_raw))
    seq = 0
    for fr in frames:
        png += chunk(b"fcTL", struct.pack(">IIIIIHHBB", seq, fr["w"], fr["h"],
                                          fr["x"], fr["y"], fr["dn"], fr["dd"],
                                          fr["dispose"], fr["blend"]))
        seq += 1
        png += chunk(b"fdAT", struct.pack(">I", seq)
                     + zlib.compress(scanlines(fr["pixels"], fr["w"], fr["h"])))
        seq += 1
    png += chunk(b"IEND", b"")
    return bytes(png), frames


def apng_bytes(rects, fdat_seqs=None, num_frames=None):
    """Small 4x4 APNG with the default image outside the animation."""
    png = bytearray(SIGNATURE)
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", 4, 4, 8, 6, 0, 0, 0))
    png += chunk(b"acTL", struct.pack(">II",
                                      num_frames if num_frames is not None else len(rects), 0))
    png += chunk(b"IDAT", zlib.compress(b"".join(b"\0" + bytes(16) for _ in range(4))))
    for i, (x, y, w, h) in enumerate(rects):
        png += chunk(b"fcTL", struct.pack(">IIIIIHHBB", i * 2, w, h, x, y, 1, 10, 0, 0))
        seq = i * 2 + 1 if fdat_seqs is None else fdat_seqs[i]
        raw = b"".join(b"\0" + bytes([1, 2, 3, 255]) * w for _ in range(h))
        png += chunk(b"fdAT", struct.pack(">I", seq) + zlib.compress(raw))
    png += chunk(b"IEND", b"")
    return bytes(png)


def run_cli(*args, cwd):
    return subprocess.run([sys.executable, str(HERE / "view.py")]
                          + [str(a) for a in args],
                          capture_output=True, text=True, cwd=cwd)


def test_fixture_sequential_and_seek():
    data, frames = build_fixture()
    hashes = baseline(frames, 16, 16)
    starts, durations = expected_times(frames)

    result = view.view(data, None, 4)
    assert [f["index"] for f in result["frames"]] == list(range(120))
    for entry in result["frames"]:
        i = entry["index"]
        assert entry["rgba_sha256"] == hashes[i], i
        assert entry["start"] == pair(starts[i]), i
        assert entry["duration"] == pair(durations[i]), i
    assert 1 <= result["stats"]["max_checkpoints"] <= 4
    assert 1 <= result["stats"]["max_replay_frames"] <= 32

    rng = random.Random(20240501)
    requests = [rng.randrange(120) for _ in range(400)]
    result = view.view(data, requests, 4)
    assert [f["index"] for f in result["frames"]] == requests
    for entry in result["frames"]:
        i = entry["index"]
        assert entry["rgba_sha256"] == hashes[i], i
        assert entry["start"] == pair(starts[i]), i
        assert entry["duration"] == pair(durations[i]), i
    assert result["stats"]["max_checkpoints"] <= 4
    assert result["stats"]["max_replay_frames"] <= 32


def test_checkpoint_budget():
    data, _frames = build_fixture()
    deep = view.view(data, [119], 4)
    assert deep["stats"]["max_checkpoints"] <= 4
    assert 2 <= deep["stats"]["max_replay_frames"] <= 32
    first = view.view(data, [0], 4)
    assert first["stats"]["max_replay_frames"] == 1
    for budget in (1, 2, 3, 4, 8):
        result = view.view(data, None, budget)
        assert result["stats"]["max_checkpoints"] <= budget, budget


def test_error_codes():
    cases = {
        "rectangle": apng_bytes([(3, 3, 2, 2)]),
        "sequence": apng_bytes([(0, 0, 1, 1), (1, 1, 1, 1)], fdat_seqs=[1, 1]),
        "truncated": apng_bytes([(0, 0, 1, 1)])[:-10],
    }
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        for keyword, blob in cases.items():
            src = tmp / (keyword + ".png")
            src.write_bytes(blob)
            proc = run_cli(src, tmp / "out.json", cwd=tmp)
            assert proc.returncode == 2, (keyword, proc.returncode, proc.stderr)
            assert keyword in proc.stderr, (keyword, proc.stderr)


def test_demo_and_static():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        proc = subprocess.run([sys.executable, str(HERE / "make_demo.py")],
                              cwd=tmp, capture_output=True, text=True)
        assert proc.returncode == 0, proc.stderr
        out = tmp / "output.json"
        proc = run_cli(tmp / "demo.png", out, cwd=tmp)
        assert proc.returncode == 0, proc.stderr
        result = json.loads(out.read_text())
        assert [f["index"] for f in result["frames"]] == [0, 1]
        assert result["frames"][0]["start"] == [0, 1]
        assert result["frames"][0]["duration"] == [1, 25]
        assert result["frames"][1]["start"] == [1, 25]
        assert result["frames"][1]["duration"] == [2, 25]
        demo_frames = [
            {"x": 0, "y": 0, "w": 3, "h": 2, "dispose": NONE, "blend": SOURCE,
             "pixels": bytes([40, 120, 240, 255]) * 6},
            {"x": 1, "y": 0, "w": 1, "h": 1, "dispose": PREVIOUS, "blend": OVER,
             "pixels": bytes([240, 40, 15, 128])},
        ]
        hashes = baseline(demo_frames, 3, 2)
        assert [f["rgba_sha256"] for f in result["frames"]] == hashes

        raw = b"".join(b"\0" + bytes([7, 8, 9, 255]) * 2 for _ in range(2))
        static = (SIGNATURE
                  + chunk(b"IHDR", struct.pack(">IIBBBBB", 2, 2, 8, 6, 0, 0, 0))
                  + chunk(b"IDAT", zlib.compress(raw))
                  + chunk(b"IEND", b""))
        (tmp / "static.png").write_bytes(static)
        proc = run_cli(tmp / "static.png", tmp / "static.json", cwd=tmp)
        assert proc.returncode == 0, proc.stderr
        result = json.loads((tmp / "static.json").read_text())
        assert len(result["frames"]) == 1
        assert result["frames"][0]["start"] == [0, 1]
        assert result["frames"][0]["duration"] == [0, 1]
        expected = hashlib.sha256(bytes([7, 8, 9, 255]) * 4).hexdigest()
        assert result["frames"][0]["rgba_sha256"] == expected


def test_request_file_and_reversal():
    data, frames = build_fixture()
    hashes = baseline(frames, 16, 16)
    starts, _durations = expected_times(frames)
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        (tmp / "fixture.png").write_bytes(data)
        order = [119, 0, 60, 60, 119, 29, 90]
        (tmp / "req.json").write_text(json.dumps({"frames": order}))
        proc = run_cli(tmp / "fixture.png", tmp / "out.json",
                       "--request", tmp / "req.json", "--budget", "4", cwd=tmp)
        assert proc.returncode == 0, proc.stderr
        result = json.loads((tmp / "out.json").read_text())
        assert [f["index"] for f in result["frames"]] == order
        for entry in result["frames"]:
            i = entry["index"]
            assert entry["rgba_sha256"] == hashes[i], i
            assert entry["start"] == pair(starts[i]), i
        assert result["stats"]["max_checkpoints"] <= 4
        assert result["stats"]["max_replay_frames"] <= 32


def main():
    tests = [value for name, value in sorted(globals().items())
             if name.startswith("test_") and callable(value)]
    for test in tests:
        test()
        print(f"PASS {test.__name__}")
    print(f"{len(tests)} tests passed")


if __name__ == "__main__":
    main()
