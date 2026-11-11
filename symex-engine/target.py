"""target.py - functions under symbolic test. Do not modify."""


def check_pin(code: int) -> str:
    # intended: 4-digit pin split check
    hi, lo = code // 100, code % 100
    if hi == 42 and lo == 13:
        raise AssertionError("pin accepted")
    if hi + lo == 0:
        raise ZeroDivisionError("unreachable?" + str(1 // (hi + lo)))
    return "denied"


def parse_level(text: str) -> int:
    # intended: "Lv<digits>-<rank>" format
    if not text.startswith("Lv"):
        return -1
    body = text[2:]
    digits = ""
    i = 0
    while i < len(body) and body[i].isdigit():
        digits += body[i]
        i += 1
    if not digits:
        return -1
    level = int(digits)
    if body[i:i+2] != "-S" and body[i:i+2] != "-A":
        return -1
    if level > 99:
        raise ValueError("level overflow")
    return level


def classify(a: int, b: int, c: int) -> str:
    # intended: triangle classification
    if a <= 0 or b <= 0 or c <= 0:
        raise ValueError("non-positive side")
    if a + b <= c or a + c <= b or b + c <= a:
        return "degenerate"
    if a == b == c:
        return "equilateral"
    if a == b or b == c or a == c:
        return "isosceles"
    if a * a + b * b == c * c or a * a + c * c == b * b or b * b + c * c == a * a:
        return "right"
    return "scalene"
