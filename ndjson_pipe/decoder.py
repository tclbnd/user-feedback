"""Streaming NDJSON decoder.

The decoder works on raw UTF-8 *bytes* so that:

* a multi-byte UTF-8 character split across two read chunks is reassembled
  transparently (we only split records on ``\\n``, never inside a character);
* every emitted record carries its exact byte offset range in the source,
  which is the unit of idempotency and conflict detection;
* a trailing partial line of a not-yet-sealed file is retained and completed
  by a subsequent :meth:`NDJsonStreamDecoder.feed` call instead of being
  parsed prematurely.

The decoder never interprets the payload. JSON parsing and validation happen
in :mod:`ndjson_pipe.normalize`, which keeps the byte/offset logic isolated.
"""

from dataclasses import dataclass
from typing import Iterator


@dataclass(frozen=True)
class RawLine:
    """One physical line from an NDJSON source.

    Attributes:
        line_no: 1-based physical line number within the source.
        start_offset: byte offset where the line content starts.
        end_offset: byte offset immediately after the terminating ``\\n``
            (equal to the next line's ``start_offset``; also the checkpoint
            position recorded after the line is durably processed).
        raw: line content bytes *without* the line terminator (a trailing
            ``\\r`` from CRLF files is stripped).
    """

    line_no: int
    start_offset: int
    end_offset: int
    raw: bytes

    @property
    def content_hash_input(self) -> bytes:
        """Bytes used for content hashing (terminator independent)."""
        return self.raw

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"RawLine(line={self.line_no}, [{self.start_offset},{self.end_offset}))"


class NDJsonStreamDecoder:
    """Incremental byte-to-line splitter for NDJSON streams.

    Usage::

        dec = NDJsonStreamDecoder()
        for chunk in read_chunks(file, chunk_size):
            for raw_line in dec.feed(chunk):
                ...  # parse and persist raw_line
        for raw_line in dec.flush(sealed=True):
            ...  # final line of a sealed file
    """

    def __init__(self, start_offset: int = 0, start_line_no: int = 0) -> None:
        self._buf = bytearray()
        # Absolute byte offset of self._buf[0]; starts at the resume position.
        self._offset = start_offset
        # Physical lines already durable before the resume position.
        self._line_no = start_line_no

    @property
    def line_count(self) -> int:
        """Number of lines emitted since construction."""
        return self._line_no

    @property
    def position(self) -> int:
        """Absolute byte offset of the first unconsumed buffered byte."""
        return self._offset

    @property
    def buffered_bytes(self) -> int:
        """Number of bytes held waiting for a newline (residual line)."""
        return len(self._buf)

    def feed(self, chunk: bytes) -> Iterator[RawLine]:
        """Consume one read chunk and yield every line it completes."""
        if not chunk:
            return
        self._buf.extend(chunk)
        yield from self._drain(flush_final=False)

    def flush(self, sealed: bool) -> Iterator[RawLine]:
        """Emit buffered residual data.

        When ``sealed`` is true the source is considered final: a trailing
        line without ``\\n`` is emitted. When false the residual line is kept
        for a future :meth:`feed` (an open, still-appended source).
        """
        if sealed and self._buf:
            yield from self._drain(flush_final=True)
        # unsealed: intentionally retain the residual partial line

    def _drain(self, flush_final: bool) -> Iterator[RawLine]:
        start = 0
        buf = self._buf
        while True:
            nl = buf.find(b"\n", start)
            if nl == -1:
                break
            yield self._emit(start, nl, has_newline=True)
            start = nl + 1
        if flush_final and start < len(buf):
            # trailing line without a newline terminator
            yield self._emit(start, len(buf), has_newline=False)
            start = len(buf)
        # compact the buffer: keep only the unconsumed residual
        if start:
            del buf[:start]
            self._offset += start

    def _emit(self, start: int, nl: int, has_newline: bool) -> RawLine:
        self._line_no += 1
        end_content = nl
        if has_newline and end_content > start and self._buf[end_content - 1] == 0x0D:
            end_content -= 1  # strip CR from CRLF
        line_start = self._offset + start
        line_end = self._offset + (nl + 1 if has_newline else nl)
        return RawLine(
            line_no=self._line_no,
            start_offset=line_start,
            end_offset=line_end,
            raw=bytes(self._buf[start:end_content]),
        )


def decode_payload(raw: bytes) -> str:
    """Decode a line payload as UTF-8, raising :class:`UnicodeDecodeError`.

    Kept separate so callers can map the error to a structured rejection.
    """
    return raw.decode("utf-8")
