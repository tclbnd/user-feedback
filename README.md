# xakit

Rescue report for the Video CD collection copied to disk as `.DAT` files
(RIFF CDXA: whole 2352-byte CD-ROM XA sectors). `xainfo.py` checks each copy
and prints one JSON summary, used to find damaged rips before conversion.

## Output

| field | meaning |
|---|---|
| `xa_file_number`, `interleaved` | from the CDXA `fmt ` chunk (XA attributes bit 13 = interleaved) |
| `sectors` | whole sectors in the data chunk |
| `trailing_bytes` | bytes after the last whole sector |
| `start_lba`, `end_lba` | logical block addresses of the first and last readable sectors (MSF 00:02:00 is LBA 0) |
| `duration` | sectors from `start_lba` to `end_lba` at 75 per second, as a fraction string |
| `modes` | sector counts by CD-ROM mode |
| `form1`, `form2` | Mode 2 sectors by XA form |
| `video`, `audio`, `data`, `empty` | Mode 2 sectors by the submode of their subheader (`empty`: none of the three) |
| `user_bytes` | user data in all readable sectors (2048 per Mode 1 or Form 1 sector, 2324 per Form 2 sector, none in a Mode 0 sector) |
| `edc_errors` | sectors whose EDC does not match (a Form 2 sector may leave its EDC at 0, meaning not computed) |
| `bad_sync` | sectors without the CD sync pattern; they are not readable and are not counted anywhere else |
| `subheader_errors` | sectors whose two subheader copies differ |
| `discontinuities` | places where a sector's address does not follow the previous one |
| `audio_format` | from the first audio sector: `channels`, `sample_rate`, `bits`, `emphasis` |
| `channels` | Mode 2 sector counts by `file:channel` from the subheader |
| `records` | records that end in the copy (a sector marked end of record or end of file) |
| `eof_lba` | address of the first sector marked end of file, else `null` |

## Layout

- `xakit/xa.py` – RIFF, sectors, EDC
- `xainfo.py` – command line

## Running

    python3 xainfo.py examples/AVSEQ01.DAT examples/MUSIC01.DAT
    python3 -m unittest discover -s tests
