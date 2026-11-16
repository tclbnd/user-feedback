# H.264 Annex-B to length-prefixed sample repack

Run: `python3 h264.py input.json output.json`

Input `{"stream":[byte...]}` — Annex B byte stream with 3-byte
(`00 00 01`) or 4-byte (`00 00 00 01`) start codes. NAL unit
header: `forbidden(1)|nri(2)|type(5)`.

Group NAL units into access units (AU): a new AU starts when a
VCL NAL (type 1..5) appears AND the previous accumulated AU
already contains a VCL NAL; AUD (type 9), SPS(7), PPS(8),
SEI(6) and other non-VCL attach to the FOLLOWING AU. Trailing
zero bytes between start code and NAL header are ignored.
A stream ending mid-NAL is corrupt: emit `"error":"truncated"`
with the byte offset of the incomplete NAL.

Output `{"aus":[{"index","nals":[{"type","bytes":len,"lenhex"}...]}...],
"configs":[{"at_au":i,"sps":<hex>,"pps":<hex>}...]}` — a config
event fires at the FIRST AU whose parameter-set payloads differ
from the currently active pair; repeated identical SPS/PPS never
re-fire. `lenhex` is the 4-byte-length-prefixed hex of the NAL.
