# rtpkit

Troubleshooting helper for the contribution links: `rtpstat.py` reads a
packet capture (`.pcap`) of an RTP audio or video feed and prints one JSON
summary per capture with one entry per RTP stream (SSRC), used to judge loss,
reordering and jitter on a link.

## Output

| field | meaning |
|---|---|
| `link_type` | the capture's link-layer type (an integer) |
| `nanosecond` | `true` for a nanosecond-resolution capture |
| `truncated_packets` | the number of packets captured shorter than they were on the wire (they are skipped) |
| `streams` | one entry per SSRC in order of first appearance |

Each stream has `ssrc` (8 lower-case hex digits), `payload_type` (of its
first packet), `packets` (distinct packets received), `lost` (expected minus
received, where expected spans the lowest to the highest extended sequence
number), `duplicates`, `out_of_order` (packets whose extended sequence
number is below the highest received before them), `payload_bytes` (RTP
payload without CSRC list, header extension and padding), `markers`,
`duration` (seconds from the first to the last distinct packet's arrival, a
fraction string) and `jitter` (the RFC 3550 interarrival jitter estimate in
timestamp units after the last packet, two decimals; `null` for payload types
without a static clock rate).

Captures may use either byte order and microsecond or nanosecond time stamps;
link layers are Ethernet (with VLAN tags), Linux cooked capture, raw IP and
BSD loopback; IPv4 and IPv6 carry UDP. RTCP packets are not RTP streams.
RTP headers, sequence number extension and the jitter estimate follow
RFC 3550; static payload type clock rates follow RFC 3551.

## Layout

- `rtpkit/rtp.py` – capture, link layers, RTP streams
- `rtpstat.py` – command line

## Running

    python3 rtpstat.py examples/studio_link.pcap examples/mixer.pcap
    python3 -m unittest discover -s tests
