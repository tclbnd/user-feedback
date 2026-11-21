"""RTP payloads from the media gateway, turned into AMR storage files."""
from .frames import AmrError, frame_size

MAGIC = {"amr-nb": b"#!AMR\n", "amr-wb": b"#!AMR-WB\n"}


def depacketize(payloads, codec="amr-nb", mode="octet-aligned"):
    """payloads: list of RTP payloads (bytes), in sequence order."""
    if codec not in MAGIC:
        raise AmrError(f"unknown codec {codec}")
    out = [MAGIC[codec]]
    for p in payloads:
        pos = 1
        toc = []
        while True:
            entry = p[pos]
            pos += 1
            toc.append(entry)
            if not entry & 0x80:
                break
        for entry in toc:
            ft = (entry >> 3) & 0x0F
            size = frame_size(ft) - 1
            out.append(bytes([entry]) + p[pos:pos + size])
            pos += size
    return b"".join(out)
