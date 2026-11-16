# ASS karaoke progress sampler

Run: `python3 ass.py input.json output.json`

Input `{"events":[...], "samples":[t_ms...]}`. An event
`{"layer","start","end","text","style"}` where `text` may contain
override tags in braces (subset); `style` is an optional object whose
`i`/`b`/`u` flags seed the event (missing or absent means all zero):
- `{\kN}` / `{\kfN}`: karaoke segment, N centiseconds of fill time for
  the text RUN after the tag (until the next tag or end).
- `{\koN}`: outline-only karaoke segment (same timing, mode `o`).
  `k`/`kf`/`ko` share one fill formula; the mode letter only tells the
  renderer how to paint the highlight.
- `{\r}`: reset, restores the event's own style AND cancels any
  pending karaoke segment, so text after it reports `"kara": null`.
- `{\i1}` `{\b1}` `{\u1}` and the matching `...0` forms: inline style
  flags that persist until switched off or reset.
- `\n`: hard line break (one output char newline), `\h`: non-breaking
  space (one output char U+00A0). Escapes are resolved after tag
  parsing, so they keep their character position inside a karaoke run.
- `{{`/`}}` are literal single braces; an unbalanced brace is literal
  too. Braced text not matching the subset is dropped, but still marks
  a span boundary.

At sample time t (ms), each event with `start <= t < end` (half-open,
so an event ending at t is gone and one starting at t is visible) is
rendered to plain text with escapes resolved. Karaoke segments chain
in text order: segment j owns the window
`[start + sum(dur_0..dur_j-1), start + sum(dur_0..dur_j)]`, and its
run gets
`fill = clamp((t - start - sum(prior durations)) / dur, 0, 1)`
so prior segments read 1, later segments 0. The value is recomputed
from absolute time on every sample: a seek mid-event never reuses a
stale highlight. A zero-duration segment (`\k0`) is a step: fill 0
before its boundary instant and 1 at/after it. Fill is rounded to
3 decimals.

Output `{"samples":[{"t","lines":[{"layer","text","spans":
[{"text","style":{"i","b","u"},"kara":{"mode","fill"}}...]}]}...]}`.
Spans carry the run's karaoke info; non-karaoke text has
`"kara":null`; `mode` is `k` | `f` | `o`; absent style flags read 0.
The span texts concatenate back to `text` exactly, preserving every
original character range. Lines sort by (layer, start, input index)
for draw order; samples keep input order, including irregular or
variable-frame-rate grids.

Tests: `python3 -m unittest test_ass -v`
