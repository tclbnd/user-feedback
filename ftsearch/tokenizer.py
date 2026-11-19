"""Tokenizer for article text: lowercase words, positions count tokens."""
import re

WORD = re.compile(r"[a-z0-9]+")
STOP = frozenset()


def tokens(text):
    return [(i, w) for i, w in enumerate(WORD.findall(text.lower())) if w not in STOP]


def terms(text):
    return [w for _, w in tokens(text)]
