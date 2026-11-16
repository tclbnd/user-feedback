# ASS karaoke progress sampler

Run: `python3 ass.py input.json output.json`

Input `{"events":[...], "samples":[t_ms...]}`. An event
`{"layer","start","end","text","style"}` where `text` may contain
override tags in braces (subset):
- `{\kN}` / `{\kfN}`: karaoke segment — N centiseconds of fill time
  for the text RUN after the tag (until the next tag or end).
- `{\koN}`: outline-only karaoke segment (same timing, mode `o`).
- `{\r}`: reset — clears style AND cancels pending karaoke timing.
- `{\i1}` `{\b1}` `{\u1}`: inline style flags (persist until \r or 0).
- `\\n`: hard line break (one output char `\n`), `\\h`: nbsp.
- Braced text not matching the subset is dropped; `{{`/`}}` are
  literal braces.

At sample time t (ms), for each event with start <= t < end: the
plain text with escapes resolved; each karaoke segment gets
`fill = clamp((t - start - sum(prior segment durations)) / dur, 0, 1)`
per segment in text order — prior segments are 1 while later are 0.
Output `{"samples":[{"t","lines":[{"layer","text","spans":
[{"text","style":{"i","b","u"},"kara":{"mode","fill"}}...]}]}...]}`
— spans carry the segment's karaoke info; non-karaoke text has
`"kara":null`; `mode` is `k` | `f` | `o`; `style` flags absent = 0.
Events sort by (layer, start, index); samples keep input order.
