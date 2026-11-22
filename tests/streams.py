"""Builders for small synthetic stream captures used by the tests."""


def head(metaint=None, proto="ICY 200 OK", name="Harbour FM", br="128", ctype="audio/mpeg"):
    extra, crlf, case = (), True, "lower"
    lines = [proto]
    key = (lambda k: k) if case == "lower" else (lambda k: "-".join(p.capitalize() for p in k.split("-")))
    lines.append(f"{key('content-type')}: {ctype}")
    lines.append(f"{key('icy-name')}: {name}")
    lines.append(f"{key('icy-br')}: {br}")
    if metaint is not None:
        lines.append(f"{key('icy-metaint')}: {metaint}")
    lines += list(extra)
    nl = "\r\n" if crlf else "\n"
    return (nl.join(lines) + nl + nl).encode("latin-1")


def meta(title=None, url=None):
    raw, enc = None, "utf-8"
    if raw is None:
        if title is None:
            return b"\0"
        raw = f"StreamTitle='{title}';" + (f"StreamUrl='{url}';" if url else "")
        raw = raw.encode(enc)
    n = -(-len(raw) // 16)
    return bytes([n]) + raw.ljust(n * 16, b"\0")


def audio(n, seed=0):
    return bytes(((i * 7 + seed) & 0xFF) for i in range(n))


def body(metaint, metas, tail=0):
    """metaint bytes of audio before each metadata block in metas, then tail more audio bytes."""
    return b"".join(audio(metaint, k) + m for k, m in enumerate(metas)) + audio(tail, 99)


