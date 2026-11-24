# sdskit

Check for the sampler library transfers: samples pulled from the studio's
hardware samplers with a MIDI Sample Dump and saved as `.syx` files.
`sdsinfo.py` prints one JSON summary per transfer, used to check the sample
format, loop settings and that every data packet arrived intact.

## Output

| field | meaning |
|---|---|
| `device_id`, `sample_number`, `bits` | from the dump header (integers) |
| `sample_rate` | in Hz from the header's sample period, as a fraction string |
| `length` | sample words the header announces (an integer) |
| `duration` | `length / sample_rate` in seconds as a fraction string |
| `loop` | `{"start", "end", "type"}` with `forward`, `alternating` or `type_N`; `null` when the loop is off |
| `packets` | the number of data packets |
| `bad_checksums` | data packets whose checksum does not match |
| `out_of_order` | data packets whose running number is not the one after the previous packet's |
| `samples`, `complete` | sample words decoded from the packets (at most `length`), and whether that is all of them |
| `first_sample` | the first sample word as a signed integer, `null` without samples |
| `peak` | the largest sample magnitude as a fraction of full scale, `%.6f`, `null` without samples |
| `handshakes` | handshake messages counted by name: `ack`, `nak`, `cancel`, `wait`, `eof` |

The dump header, data packets (120 data bytes each), checksums, the packing
of sample words into 7-bit bytes and their number format, and the handshake
messages follow the MIDI Sample Dump Standard. A transfer file may hold other
SysEx messages too.

## Layout

- `sdskit/sds.py` – messages, header, packets, samples
- `sdsinfo.py` – command line

## Running

    python3 sdsinfo.py examples/strings_c3.syx examples/drum_loop.syx
    python3 -m unittest discover -s tests
