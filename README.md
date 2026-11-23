# ifokit

Check for the DVD masters of the back catalogue: the title set information
file (`VTS_xx_0.IFO`) of each disc is read before re-authoring. `ifoinfo.py`
prints one JSON summary per file, used to compare running times, streams and
chapter points with the delivery paperwork.

## Output

| field | meaning |
|---|---|
| `standard`, `aspect` | `NTSC` or `PAL`, `4:3` or `16:9`, from the title set video attributes |
| `audio` | per audio stream: `codec` (`AC-3`, `MPEG-1`, `MPEG-2`, `LPCM`, `DTS`, else `type_N`), `channels`, `sample_rate`, `language` (ISO 639 code, `null` when the stream gives none), `bits` (LPCM quantisation 16, 20 or 24, `null` for other codecs or dynamic range control), `application` (`karaoke`, `surround` or `null`), `purpose` (code extension: `normal`, `visually_impaired`, `directors_comments`, `alternate_directors_comments`, `null` when unspecified) |
| `subpictures` | language code of each subpicture stream (`null` when none is given) |
| `pgcs` | per program chain: `programs`, `cells`, `duration` (the chain's playback time), `cell_total` (sum of the cell playback times, counting an angle block once) and `program_starts` (start of each program within the chain) and `still_cells` (cells with a still time after them) |
| `titles` | per title: `chapters` and `chapter_starts` (the start of each chapter's program within its chain) |

Times are seconds as fraction strings. Playback times are stored as BCD
hours, minutes, seconds and frames; the frame byte also carries the frame
rate. Table locations in the header are sector numbers (2048 bytes).

## Layout

- `ifokit/ifo.py` – header, attributes, program chains, chapters
- `ifoinfo.py` – command line

## Running

    python3 ifoinfo.py examples/VTS_01_0.IFO examples/VTS_02_0.IFO
    python3 -m unittest discover -s tests
