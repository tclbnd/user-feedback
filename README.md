# aviscan

Older surveillance recorders write AVI. To cut evidence clips without
re-encoding we read each stream's header and the chunk index: for every
stream the number of chunks, how many are key frames, the first entries
(`offset` is the absolute file position of the chunk data, `size` its
length) and, for video, the duration from the stream's rate.

## Layout

- `aviscan/riff.py` – chunk tree
- `aviscan/index.py` – stream headers and index
- `aviscan/clips.py` – clip bounds
- `aviinfo.py` – command line

## Running

    python3 aviinfo.py examples/*.avi
    python3 -m unittest discover -s tests
