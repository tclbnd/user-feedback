# wmfkit

Check for the Windows Metafile (`.wmf`) clip art in the print shop's
template library. `wmfinfo.py` prints one JSON summary per file, used to
catch broken or oddly sized clip art before it is offered to customers.

## Output

| field | meaning |
|---|---|
| `placeable` | `true` when the file starts with the placeable (Aldus) header |
| `bounds` | `[left, top, right, bottom]` from the placeable header, `null` without one |
| `inch` | logical units per inch from the placeable header, `null` without one |
| `size_mm` | `[width, height]` of the bounds in millimetres, strings with one decimal, `null` without a placeable header |
| `checksum_ok` | whether the placeable header's checksum is right, `null` without one |
| `version` | metafile version, `"1.0"` or `"3.0"` |
| `records` | the number of records read, the end-of-file record included |
| `eof` | `true` when the end-of-file record was found |
| `size_ok`, `max_record_ok` | whether the metafile header's file size and largest record size match the records |
| `objects` | number of objects from the metafile header |
| `window_origin`, `window_extent` | `[x, y]` from the last SetWindowOrg / SetWindowExt record, `null` without one |
| `polygons`, `polygon_points` | how many Polygon records and how many points they hold |
| `texts` | the strings of the TextOut and ExtTextOut records in order (Windows-1252) |

The placeable header, the metafile header and the records follow the
Windows Metafile format ([MS-WMF]).

## Layout

- `wmfkit/wmf.py` – headers and records
- `wmfinfo.py` – command line

## Running

    python3 wmfinfo.py examples/logo.wmf examples/sign.wmf
    python3 -m unittest discover -s tests
