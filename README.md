# amrfile

The voicemail platform receives messages from the mobile core as AMR and
AMR-WB files. For each message we report the codec, sample rate, channel
count, number of 20 ms frame blocks and duration, how many frames carry
speech, comfort noise (SID) or no data, how many were flagged as damaged,
and how often each speech mode was used (keyed by bit rate in kbit/s).

Messages left through the SIP gateway arrive as RTP payloads instead of
files. `rtp2amr.py` writes one call leg as a storage-format file, using the
payload format the session negotiated (octet-aligned or bandwidth-efficient,
no interleaving, no payload CRC). Both formats carry the same speech bits, so
the two produce byte-identical files.

## Output conventions

- Duration is 20 ms per frame block; an incomplete block at the end of a file
  is not counted.
- Frame blocks are accounted for as speech, SID (comfort noise), no data or
  damaged, following RFC 4867 / TS 26.101 / TS 26.201. A block whose Q bit is
  0, and AMR-WB's speech-lost type, count as damaged rather than speech.
- Speech modes are keyed by bit rate in kbit/s, for both AMR-NB and AMR-WB.

## Layout

- `amrfile/frames.py` – storage format frames
- `amrfile/summary.py` – message summary
- `amrfile/rtp.py` – RTP payloads to storage format
- `amrfile/mailbox.py` – quota accounting
- `amrinfo.py` – command line
- `rtp2amr.py` – call leg to file

## Running

    python3 amrinfo.py examples/*.amr examples/*.awb
    python3 -m unittest discover -s tests
