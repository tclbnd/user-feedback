import struct
from .format import encode_frame, HEADER_SIZE

# Deterministic sample-stream generator with 3 known corruptions.
# Payload of each frame: 64 little-endian int16 audio samples.
# sample[i] of frame f = ((f * 64 + i) * 37 + 11) mod 2000 - 1000

N_FRAMES = 12
SAMPLES_PER_FRAME = 64


def make_payload(f):
    vals = [((f * SAMPLES_PER_FRAME + i) * 37 + 11) % 2000 - 1000
            for i in range(SAMPLES_PER_FRAME)]
    return struct.pack("<64h", *vals)


def original_audio():
    # The full original audio: concatenation of all frame payloads.
    return b"".join(make_payload(f) for f in range(N_FRAMES))


def build_clean():
    frames = [encode_frame(f, make_payload(f)) for f in range(N_FRAMES)]
    return frames, b"".join(frames)


def _flip(buf, pos):
    buf[pos] ^= 0xFF


def build_corrupted():
    # Returns (stream_bytes, truth) where truth records the 3 corruptions:
    #   1. frame 3: header corruption (flip a byte inside the seq field)
    #   2. frame 6: payload corruption (flip a payload byte)
    #   3. frame 9: header corruption (flip a byte in the length field)
    frames, _ = build_clean()
    size = HEADER_SIZE + SAMPLES_PER_FRAME * 2
    offs = [i * size for i in range(N_FRAMES)]

    corrupted = list(frames)

    b3 = bytearray(frames[3])
    _flip(b3, 5)            # inside seq field -> header CRC fails
    corrupted[3] = bytes(b3)

    b6 = bytearray(frames[6])
    _flip(b6, HEADER_SIZE + 10)   # payload byte -> payload CRC fails
    corrupted[6] = bytes(b6)

    b9 = bytearray(frames[9])
    _flip(b9, 9)            # inside length field -> header CRC fails
    corrupted[9] = bytes(b9)

    stream = b"".join(corrupted)
    truth = {
        "frame_size": size,
        "offsets": offs,
        "corruptions": [
            {"frame": 3, "kind": "bad_header", "flipped_abs": offs[3] + 5},
            {"frame": 6, "kind": "bad_payload",
             "flipped_abs": offs[6] + HEADER_SIZE + 10},
            {"frame": 9, "kind": "bad_header", "flipped_abs": offs[9] + 9},
        ],
        "expected_status": {
            0: "ok", 1: "ok", 2: "ok", 3: "bad_header",
            4: "ok", 5: "ok", 6: "bad_payload",
            7: "ok", 8: "ok", 9: "bad_header",
            10: "ok", 11: "ok",
        },
    }
    return stream, truth
