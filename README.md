# DASH multi-period segment map

Run: `python3 dash.py input.json output.json`

Input: `periods` in play order, each `{"id","duration"?,"tracks":[...]}`;
`duration` is the period length in SECONDS and may be absent on the
last period (open tail). Each track `{"id","timescale","pto",
"timeline":[{"t"?,"d","r"?}...]}` — timescale ticks: `t` optional
explicit start (defaults to the running end of the previous entry,
or 0 for the first), `d` per-segment duration, `r` repeat count:
r>=0 means r+1 segments; r=-1 means repeat until the next entry's
`t` boundary, or, when last, until the period's own duration; r<-1
is invalid input and will not appear. pto is the
presentationTimeOffset subtracted from media time before mapping to
real seconds.

Each segment occupies the half-open real-time interval
`[period_start + (m - pto)/timescale, +d/timescale)`. `period_start`
is the cumulative sum of earlier `duration` values (missing duration
extends the period to its last segment end). Segments are clipped so
no segment start crosses the period end; a segment may still run past
it only if it STARTS before it.

Output `{"tracks":{tid:{"segments":[{"n","start","end"}...]}},
"periods":[{id,"start","end"}], "seeks":[{"t","track","n"|{"gap":true}}]}`
— n is the 0-based segment index on that track; a seek uses
start<=t<end, and a time landing between the last segment end and the
next segment start reports `{"gap":true}` instead of n. Seek targets
are given per `{"t","track"}` in input order. Fractions must round to
6 decimals.
