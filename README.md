# aviscan

Older surveillance recorders write AVI. To cut evidence clips without
re-encoding we read each stream's header and the chunk index: for every
stream the number of chunks, how many are key frames, the first entries
(`offset` is the absolute file position of the chunk data, `size` its
length) and, for video, the duration from the stream's rate.

Notes:

- `rate` is `strh.dwRate / strh.dwScale` from the stream header, so 29.97 fps
  recordings report `30000/1001` and durations do not drift.
- Both the classic `idx1` index and the OpenDML `indx`/`ix##` indexes are
  read; OpenDML entries win when a file carries both.
- `idx1` offsets are documented as relative to the `movi` fourcc, but some
  recorders write file-relative or data-relative offsets. The convention is
  detected by checking the entries against the real chunk headers.
- Key frames come from the index flags (`AVIIF_KEYFRAME` in `idx1`, bit 31
  of `dwSize` in a standard index).
- Odd-sized chunks are word aligned, multi-RIFF (`AVIX`) segments are
  followed, and files are memory-mapped so multi-gigabyte recordings work.

## Layout

- `aviscan/riff.py` – chunk tree
- `aviscan/index.py` – stream headers and index
- `aviscan/clips.py` – clip bounds
- `aviinfo.py` – command line

## Running

    python3 aviinfo.py examples/*.avi
    python3 -m unittest discover -s tests
