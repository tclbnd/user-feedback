# hevckit

Ingest checker for the HEVC (H.265) mezzanine files delivered as raw Annex B
streams (`.h265`, `.hevc`). `hevcinfo.py` scans each stream and prints one JSON
summary, used to route files to the right transcoding ladder.

## Output

| field | meaning |
|---|---|
| `nal_units` | NAL unit counts by type: `VPS`, `SPS`, `PPS`, `AUD`, `EOS`, `EOB`, `FD`, `SEI_PREFIX`, `SEI_SUFFIX`, `VCL` (types 0–31), `other` |
| `pictures` | pictures in the base layer (VCL NAL units that start a picture) |
| `sps` | one entry per SPS id (a later SPS with the same id replaces the earlier one), sorted by id |

SPS fields:

| field | meaning |
|---|---|
| `id`, `profile_idc`, `tier` (`main`/`high`) | |
| `level` | general level as written in the spec, e.g. `4`, `5.1` |
| `max_sub_layers` | temporal sub-layers |
| `chroma_format`, `separate_colour_planes` | `4:0:0`, `4:2:0`, `4:2:2`, `4:4:4` |
| `width`, `height` | output picture size after the conformance window |
| `coded_width`, `coded_height` | `pic_width/height_in_luma_samples` |
| `bit_depth_luma`, `bit_depth_chroma` | |
| `max_dec_pic_buffering` | DPB size in pictures for the highest sub-layer |
| `scaling_lists`, `sao`, `temporal_mvp` | tool flags |
| `short_term_ref_pic_sets` | number of reference pictures (delta POCs) in each short-term RPS of the SPS |
| `long_term_ref_pics` | long-term reference pictures signalled in the SPS |
| `sar` | sample aspect ratio `w:h` from the VUI, else `null` |
| `full_range`, `colour_primaries`, `transfer_characteristics`, `matrix_coeffs` | VUI video signal type, else `null` |
| `frame_rate` | from the VUI timing info, frames per second as a fraction string, else `null` |

## Layout

- `hevckit/hevc.py` – NAL units and SPS
- `hevcinfo.py` – command line

## Running

    python3 hevcinfo.py examples/feature_uhd.h265 examples/promo_1080.h265
    python3 -m unittest discover -s tests
