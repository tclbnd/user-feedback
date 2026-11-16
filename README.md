# HEVC leading-picture filter

Run: `python3 hevc.py input.json output.json`

Input: `access_units` in DECODE order, each `{"poc","type"}` where type is
`IDR` | `CRA` | `RADL` | `RASL` | `TRAIL`; `entries`: `{"index","poc"}` —
random-access entry points requested (each must land on an IDR or CRA AU).

Rules: produce the OUTPUT list per requested entry — the subset of AUs
starting at that entry's index that the decoder may emit, in DECODE
order, until the next listed entry's index (each entry owns the slice
from its index to the next entry's index or stream end). At an IDR
entry: emit entry AU and everything after; any leading pictures stored
before it are discarded unconditionally. At a CRA entry: RADL AUs are
emitted only when their `poc` is GREATER than the poc of the previous
listed entry's own... spec rule applied here: a RADL is kept iff its
poc > the CRA's poc AND poc < the poc of the first following TRAIL;
RASL AUs are always skipped; skipped AUs are reported with reason
`rasl` | `radl-poc-out-of-range` | `pre-idr`. TRAIL AUs always emit.
An entry index that is not IDR/CRA yields `{"entry","error":"not-irap"}`
while other entries still resolve. Output
`{"entries":[{"entry","poc","output":[poc...],"skipped":[{"poc","reason"}]}...]}`.
