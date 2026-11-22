# av1kit

Ingest checker for the AV1 test clips the encoder farm writes as IVF files.
`av1info.py` prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `fourcc`, `width`, `height` | from the IVF file header |
| `timebase` | IVF time base as `numerator/denominator` seconds per tick |
| `ivf_frames` | complete IVF frames in the file; `header_frame_count` is the count written in the header |
| `duration` | (last pts − first pts + the pts step of the first two frames) × time base, in seconds as a fraction string |
| `obus` | OBUs by type name (`temporal_delimiter`, `sequence_header`, `frame`, `padding`, …) |
| `temporal_units` | temporal delimiter OBUs |
| `layers` | distinct (temporal_id, spatial_id) pairs seen in OBU extension headers (0, 0 without an extension) |
| `sequence` | the first sequence header: `profile`, `still_picture`, `reduced_still_picture_header`, `timing` (`num_units_in_display_tick`, `time_scale`, `ticks_per_picture` or `null`), `operating_points`, `level` and `tier` of operating point 0, `max_width`, `max_height`, `superblock_128`, `order_hint_bits` (`null` when order hints are off), `bit_depth`, `mono_chrome`, `color_primaries`, `transfer_characteristics`, `matrix_coefficients`, `color_range` (full range), `subsampling` (`4:2:0`, `4:2:2`, `4:4:4` or `mono`), `film_grain` |

## Layout

- `av1kit/av1.py` – IVF frames, OBU headers, sequence header
- `av1info.py` – command line

## Running

    python3 av1info.py examples/trailer_1080p.ivf examples/svc_720p.ivf
    python3 -m unittest discover -s tests
