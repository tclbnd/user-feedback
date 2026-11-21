"""Builders for small synthetic modules used by the tests."""
def sample(name="", words=0, finetune=0, volume=64, rep_start=0, rep_len=1):
    return (name, words, finetune, volume, rep_start, rep_len)


def module(title="", samples=(), orders=(0,), patterns=None, channels=4, tag=b"M.K.", fifteen=False, song_length=None,
           extra_orders=()):
    """patterns: list of dicts {(row, channel): (sample, period, effect, param)}."""
    n = 15 if fifteen else 31
    out = bytearray(title.encode("latin-1")[:20].ljust(20, b"\0"))
    samples = list(samples) + [sample()] * (n - len(samples))
    for name, words, fine, vol, rs, rl in samples[:n]:
        out += name.encode("latin-1")[:22].ljust(22, b"\0") + words.to_bytes(2, "big") + bytes([fine & 0x0F, vol])
        out += rs.to_bytes(2, "big") + rl.to_bytes(2, "big")
    table = list(orders) + list(extra_orders)
    out += bytes([len(orders) if song_length is None else song_length, 127])
    out += bytes(table + [0] * (128 - len(table)))
    if not fifteen:
        out += tag
    patterns = patterns or [{}]
    for pat in patterns:
        for row in range(64):
            for ch in range(channels):
                s, period, eff, par = pat.get((row, ch), (0, 0, 0, 0))
                out += bytes([(s & 0xF0) | (period >> 8), period & 0xFF, ((s & 0x0F) << 4) | eff, par])
    for name, words, *_ in samples[:n]:
        out += bytes((i * 7) & 0x7F for i in range(words * 2))
    return bytes(out)
