# flvscan

Live channels are archived as FLV. Moderation tools need a summary of each
recording: onMetaData, the video codec, how many video frames and key frames
it holds, the presentation times of the first frames (ms, decode time plus
composition offset), the largest tag timestamp and the audio codec.

## Layout

- `flvscan/tags.py` – header and tags
- `flvscan/amf.py` – AMF0 values
- `flvscan/summary.py` – recording summary
- `flvscan/segments.py` – review segments
- `flvinfo.py` – command line

## Running

    python3 flvinfo.py examples/*.flv
    python3 -m unittest discover -s tests
