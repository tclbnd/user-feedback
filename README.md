# icckit

Check for the ICC colour profiles (`.icc`, `.icm`) shipped with the photo
lab's printer and monitor drivers. `iccinfo.py` prints one JSON summary per
profile, used to make sure each profile is intact before it is deployed.

## Output

| field | meaning |
|---|---|
| `version` | profile version as `"major.minor.bugfix"` |
| `class` | `input`, `display`, `output`, `device_link`, `color_space`, `abstract` or `named_color` |
| `color_space`, `pcs` | data colour space and connection space signatures, trailing spaces removed (`RGB`, `GRAY`, `XYZ`, `Lab`, …) |
| `intent` | `perceptual`, `relative_colorimetric`, `saturation` or `absolute_colorimetric` |
| `illuminant` | the header's PCS illuminant `[X, Y, Z]`, `%.4f` strings |
| `size_ok` | whether the header's profile size equals the file size |
| `tags` | number of tag table entries |
| `shared_tags` | how many entries point at the same data as an earlier entry |
| `description`, `copyright` | the `desc` and `cprt` texts (for multi-localized text the en-US record, otherwise the first record), `null` when missing |
| `white_point` | `wtpt` as `[X, Y, Z]` (`%.4f` strings), `null` when missing |
| `chad` | the chromatic adaptation matrix as 9 `%.4f` strings in row order, `null` when missing |
| `trc` | tone curves present among `red`, `green`, `blue`, `gray`: `identity`, `gamma G` (`%.4f`), `table N` (N entries) or `para T: p1 p2 …` (function type and its parameters, `%.4f`) |

The header, the tag table, the number formats and the tag types follow the
ICC profile specification (ICC.1, versions 2 and 4).

## Layout

- `icckit/icc.py` – header, tags, tag types
- `iccinfo.py` – command line

## Running

    python3 iccinfo.py examples/monitor.icc examples/gray.icc
    python3 -m unittest discover -s tests
