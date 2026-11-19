"""Search result snippets for the site search page."""
from .tokenizer import WORD


def snippet(text, query, width=8):
    words = set(WORD.findall(query.lower()))
    toks = text.split()
    for i, tok in enumerate(toks):
        if tok.lower().strip(".,;:!?") in words:
            lo = max(0, i - width // 2)
            part = toks[lo:lo + width]
            return " ".join(f"[{t}]" if t.lower().strip(".,;:!?") in words else t for t in part)
    return " ".join(toks[:width])
