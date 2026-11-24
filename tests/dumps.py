"""Tiny YM6 dumps for the tests: non-interleaved, no digidrums, little numbers in the header."""


def dump(title=b"Test"):
    head = b"YM6!LeOnArD!" + bytes(22) + title + b"\0Me\0\0"
    return head + bytes(16 * 0) + b"End!"
