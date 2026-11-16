# Speaker label remap

Run: `python3 remap.py input.json output.json`

Input JSON: `target_speakers` (ordered speaker ids of the playback layout),
`segments` (time-ordered, non-overlapping), each with `start`, `end`
(sample range, half-open), `source_speakers` (ordered speaker ids delivered
by the container for that segment), and `samples` (array of channel arrays,
parallel to source_speakers, each a list of ints).

Rules: build a speaker-id mapping per segment — output channel position p
takes the source channel whose speaker id equals target_speakers[p]; if no
source carries that id the output is silence filled at the *target*
position (index -1). Duplicate speaker ids in a segment make that segment
`conflict` and its output muted. A source layout that cannot be matched to
any target position at all marks the segment `unknown` (all outputs -1)
but is reported, not guessed by alphabetical order. Output:
`{"channels":[[...sample values per target position concatenated...]],
 "segment_report":[{"status":"ok"|"conflict"|"unknown","map":[source idx or -1]}]}`.
