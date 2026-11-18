# MPEG-TS clock recovery

Run: `python3 ts.py input.json output.json`

Input: `packets` ordered, each `{"pid","program","pcr":int|null,
"pts":int|null}` — pcr/pts are raw 33-bit counter ticks (wrap modulus
2^33, 90kHz). Output must unwrap to a continuous 64-bit timeline.

Rules: maintain an unwrap epoch PER PROGRAM. For each packet that
carries pcr: if the raw pcr jumps backward by more than half the
modulus (2^32) vs the program's previous pcr, bump epoch += 2^33;
if it jumps FORWARD by more than half the modulus (backward
discontinuity without wrap — e.g. source restarted), reset the
epoch to make this pcr the new baseline and record a `discontinuity`
event (never let output go non-monotonic within a program). pts is
unwrapped against the same program epoch (a pts may legitimately
sit slightly BEFORE the current pcr — up to one wrap-half lookahead
when pcr races ahead of pts near a wrap). A packet whose `program`
differs from the previous packet's program is a program switch:
log it, but each program keeps its own epoch — do NOT reset the new
program's clock to the old one's. Output `{"packets":[{"pts64",
"pcr64"} per packet (null when absent)], "programs":{pid:{epoch,
last_pcr}}, "discontinuities":[{"index","kind":"wrap"|"reset"|
"program_switch","detail"}]}` with pts64/pcr64 in 90kHz ticks and a
`ms` convenience field = ticks/90 rounded 3 decimals.
