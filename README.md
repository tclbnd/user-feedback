# sdpkit

Check for the stream descriptions (`.sdp`) of the plant's AES67 and SMPTE ST
2110 audio senders, collected from the devices before each show. `sdpinfo.py`
reads each description and prints one JSON summary, used to confirm addresses,
formats, packet timing and the PTP reference.

## Output

| field | meaning |
|---|---|
| `session` | the s= line |
| `media` | one entry per m= line |

Each media entry has `type`, `port`, `ports` (port count from `port/count`),
`protocol`, `address`, `ttl` and `addresses` (from the c= line that applies:
the media's own, else the session's; IPv4 multicast `addr/ttl[/count]`, IPv6
`addr[/count]`), `direction` (`sendrecv`, `sendonly`, `recvonly`, `inactive`;
media attribute, else session attribute, else `sendrecv`), `refclk` (ts-refclk,
media else session), `mediaclk`, `bandwidth_kbps` (b=AS), `payloads` (per
format: `pt`, `encoding`, `clock_rate`, `channels`, `fmtp` parameters; static
payload types need no rtpmap; audio channels default to 1), `ptime`
(milliseconds as a fraction string), `packet_bytes` (audio payload bytes per
packet for L16, L24 and AM824 at the first format) and `packets_per_second`
(fraction string).

Each media entry also has `ptp` (from a `ptp=` reference clock: `version`,
`grandmaster` in upper case, `domain` (0 when not given) and `traceable`; for
`ptp=<version>:traceable` the grandmaster and domain are `null`; `null` for
other clocks or none), `rtp_offset` (the number after `direct=` in mediaclk,
else `null`) and `source` (the first source address of an `incl`
source-filter, media attribute else session attribute, else `null`).

Each media entry also has `ordered_channels`: the number of audio channels the
first format's `channel-order` fmtp parameter describes, per the SMPTE ST
2110-30 channel grouping symbols (convention `SMPTE2110`); `null` when there is
no such parameter, it uses another convention, or a symbol is not defined.

## Layout

- `sdpkit/sdp.py` – lines, connection data, attributes, media
- `sdpinfo.py` – command line

## Running

    python3 sdpinfo.py examples/console_out.sdp examples/stagebox.sdp
    python3 -m unittest discover -s tests
