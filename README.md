# y4mlab

The encoder test lab keeps its reference clips as YUV4MPEG2 (`.y4m`). Before
a clip enters a test run we summarise it: frame size, rate, interlacing,
sample aspect, chroma format and bit depth, plane sizes, frame count and the
mean luma of every frame (in sample units, so 10-bit clips read 0..1023).

## Layout

- `y4mlab/stream.py` – header, plane layout, frames
- `y4mlab/probe.py` – clip summary
- `y4mlab/psnr.py` – PSNR between luma planes
- `y4minfo.py` – command line

## Running

    python3 y4minfo.py examples/*.y4m
    python3 -m unittest discover -s tests
