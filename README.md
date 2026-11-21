# tskit

Checker for broadcast captures (MPEG-2 transport streams from the DVB tuners
and Blu-ray style `.m2ts` exports). `tsinfo.py` prints one JSON summary per file.

## Output

| field | meaning |
|---|---|
| `packet_size` | 188, 192 (4-byte timestamp prefix) or 204 (16 trailing parity bytes) |
| `packets` | all packets in the file |
| `tei` | packets with the transport error indicator set (otherwise ignored) |
| `null` | null packets |
| `bad_sections` | PSI sections whose CRC does not match (ignored) |
| `cc_errors` | continuity counter discontinuities over all PIDs |
| `pcr` | packets carrying a PCR |
| `programs` | programs from the PAT, by program number |

Per program: `program`, `pmt_pid`, `pcr_pid`, `pcr_count` (PCRs carried on the
PCR PID) and `streams` in PMT order. Per stream: `pid`, `stream_type`,
`packets` (packets with payload), `pes` (PES packets started), `first_pts`,
`last_pts` (raw 33-bit values), `cc_errors`, `duration` (time from the first to
the last PTS in seconds as a fraction string, across 33-bit wraparound).

## Layout

- `tskit/ts.py` – packets, PSI sections, PES headers
- `tsinfo.py` – command line

## Running

    python3 tsinfo.py examples/dvb_mux.ts examples/disc.m2ts
    python3 -m unittest discover -s tests
