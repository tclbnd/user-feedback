"""Decoding of the frames the catalogue uses."""

_CODECS = {0: "latin-1", 1: "utf-16-le", 2: "utf-16-be", 3: "utf-8"}


def text(body):
    codec = _CODECS.get(body[0], "latin-1")
    return [body[1:].decode(codec, "replace").rstrip("\0")]


def comment(body):
    codec = _CODECS.get(body[0], "latin-1")
    lang = body[1:4].decode("latin-1")
    desc, _, value = body[4:].decode(codec, "replace").partition("\0")
    return {"lang": lang, "desc": desc, "text": value.rstrip("\0")}


def user_text(body):
    codec = _CODECS.get(body[0], "latin-1")
    desc, _, value = body[1:].decode(codec, "replace").partition("\0")
    return desc, value.rstrip("\0")
