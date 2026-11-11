# Event recording

This baseline packages a single, complete recording generation into an HLS EVENT playlist. It uses Python 3.9+ and an FFmpeg build with libx264 and AAC for fixtures. Set FFMPEG to an executable path if it is unavailable on PATH.

```sh
python3 fixtures.py sample
python3 recorder.py sample/capture.jsonl published
python3 -m unittest discover -s tests -v
```

The JSONL log uses three event types. All relative media paths are resolved against the directory containing the log.

- `open` defines the `epoch` and each track's initialization path, `timescale` and `clock_origin` in track ticks. Both origins map to the same recording instant. The baseline handles one epoch with zero origins.
- `fragment` names the `epoch`, `track` (audio or video), one-based `seq`, media `path` and `sha256`. Each path contains a single moof and mdat. An arrival can precede up to four earlier sequence numbers of the same track.
- `seal` defines each track's final expected sequence number in the `last` mapping. In the baseline every fragment arrives in order before sealing.

The modules cover input (`ingest.py`), muxing (`mux.py`), output publication (`playlist.py`) and the final progress record (`state.py`). The generated video has closed GOPs and B frames. The audio and video use distinct clock scales. The normal-path test decodes the resulting audio and video.
