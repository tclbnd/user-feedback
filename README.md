# dlskit

Audit of the instrument banks (Downloadable Sounds, `.dls`) shipped with the
studio's games and music tools. `dlsinfo.py` reads each bank and prints one
JSON summary per bank.

## Output

| field | meaning |
|---|---|
| `version` | the `vers` chunk as four dot-separated numbers `a.b.c.d`, `null` without one |
| `name` | the bank's INAM text, `null` without one |
| `waves` | the number of waves in the wave pool |
| `wave_frames` | the number of sample frames in all waves (data bytes divided by the wave's block alignment) |
| `instruments` | one entry per instrument in file order |

Each instrument has `name` (its INAM text or `null`), `bank_msb` and `bank_lsb`
(the MIDI bank select values, integers 0–127), `program` (0–127), `drums`
(`true` for a drum kit) and `regions`: one entry per region in file order with
`keys` (`[low, high]` key range), `wave` (index of the wave the region plays
within the wave pool, counting from 0; `null` when it cannot be resolved) and
`looped` (`true` when the region's sample information has a loop),
`unity_note` (integer), `gain_db` (the gain in decibels, `%.2f`) and `loop`
(the first loop as `{"type", "start", "length"}` with `type` `forward` or
`release`, else `null`), all from the sample information that applies to the
region (`null` when none does).

Instrument, region and wave chunks follow the DLS Level 1 and Level 2
specifications; banks from both levels are in use.

## Layout

- `dlskit/dls.py` – RIFF chunks, instruments, regions, wave pool
- `dlsinfo.py` – command line

## Running

    python3 dlsinfo.py examples/gm_lite.dls examples/kit_dls2.dls
    python3 -m unittest discover -s tests
