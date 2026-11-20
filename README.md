# spsinfo

The transcoding farm reads the first sequence parameter set of every H.264
upload to decide the encoding ladder, whether to deinterlace and how to
signal colour and aspect: profile, level, chroma format and bit depth, the
displayed (cropped) frame size, interlacing, sample aspect ratio, frame rate
and video signal range. `fps` is the frame rate as an exact fraction.

## Layout

- `spsinfo/annexb.py` – NAL units from a byte stream
- `spsinfo/bits.py` – bit reader, Exp-Golomb
- `spsinfo/sps.py` – SPS and VUI parsing
- `spsinfo/ladder.py` – encoding ladder
- `h264info.py` – command line

## Running

    python3 h264info.py examples/*.h264
    python3 -m unittest discover -s tests
