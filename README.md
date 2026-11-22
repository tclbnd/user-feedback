# icykit

Monitoring tool for the internet radio relays. The relay records the first
minutes of every upstream connection; `icyinfo.py` reads such a capture (the
raw response bytes, headers included) and prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `protocol`, `status` | `ICY`, `HTTP/1.0` or `HTTP/1.1`, and the status code |
| `name`, `genre`, `bitrate`, `content_type` | from the `icy-name`, `icy-genre`, `icy-br` and `Content-Type` headers (`null` if absent) |
| `metaint` | audio bytes between metadata blocks (`icy-metaint`), `null` when the stream has no metadata |
| `audio_bytes` | audio bytes in the capture (metadata is not audio) |
| `metadata_blocks` | metadata blocks that carry text (a zero length byte is no block) |
| `titles` | each change of `StreamTitle`: `at_audio_byte` (audio bytes before it), `title`, `url` (`StreamUrl` or `null`) |
| `truncated` | the capture ends inside a metadata block |

Header names are case-insensitive; when a header repeats, the first one counts,
and a line starting with whitespace continues the header above it. The body is
decoded as HTTP/1.1 specifies: chunked when `chunked` is the last transfer
coding, otherwise up to `Content-Length` when the server sends one (bytes after
it are not part of this response), otherwise to the end of the capture.
Metadata text is UTF-8, or Latin-1 when it is not valid UTF-8. A title may
contain quotes and semicolons; the value ends at the quote followed by `;`.

## Layout

- `icykit/icy.py` – headers, metadata blocks
- `icyinfo.py` – command line

## Running

    python3 icyinfo.py examples/harbour_fm.bin examples/night_jazz.bin
    python3 -m unittest discover -s tests
