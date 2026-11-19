"""regexp_replace(source, pattern, replacement [, flags])."""
import re


def regexp_replace(s, pattern, repl, flags=""):
    if s is None or pattern is None or repl is None:
        return None
    reflags = re.IGNORECASE if "i" in flags else 0
    repl = re.sub(r"\\(\d)", r"\\\1", repl)
    return re.sub(pattern, repl, s, flags=reflags)
