"""Incremental byte-level NDJSON line splitter.

Operates on raw bytes so that:
  * a multi-byte UTF-8 character may span read chunks (we only decode
    complete lines, never partial bytes),
  * byte offsets reported to the store are exact source offsets,
  * memory stays bounded by the longest single line, not the source size.

A trailing partial line (source not yet sealed) stays in the buffer and is
completed by a later feed(); it becomes a final line only on seal.
"""


class ByteLineSplitter:
    def __init__(self):
        self._buf = b""

    def feed(self, data):
        """Consume a chunk of bytes; return the list of now-complete lines
        (each without the trailing newline, in order)."""
        self._buf += data
        lines = []
        buf = self._buf
        start = 0
        while True:
            idx = buf.find(b"\n", start)
            if idx < 0:
                break
            lines.append(buf[start:idx])
            start = idx + 1
        self._buf = buf[start:]
        return lines

    def tail(self):
        """The unterminated remainder (empty when the source ended cleanly
        on a newline)."""
        return self._buf
