"""yuvkit - colour space conversion with explicit range and standard handling.

Quick start::

    from yuvkit import Converter
    c = Converter("bt709", rgb_range="full", yuv_range="limited")
    c.rgb_to_yuv((255, 0, 0)).codes      # -> (63, 102, 240)

Design rules enforced throughout:

* the colour matrix is applied on normalised values; range scaling is a
  separate, later step (and a separate, earlier step on the way in),
* matrix constants and chroma gains are always derived from the same
  Kr/Kb pair, so a BT.709 conversion cannot pick up BT.601 weights,
* everything is exact rational arithmetic, so the LUT and matrix paths
  agree bit for bit and differ from a float implementation only at
  rounding boundaries.
"""

from .chroma import ChromaLayout, get_layout
from .converter import (Converter, ConversionError, EPSILON, PixelResult,
                        compare_all, compare_paths)
from .lossless import (classify_roundtrip, clipping_rules, lossless_channels,
                       TOLERANCE)
from .matrix import ColorMatrix, MixedStandardError, get_matrix
from .ranges import FULL, LIMITED, SampleRange, get_range
from .request import run_file, run_request
from .standards import Standard, get_standard
from .trace import RangeTags, Trace

__version__ = "2.0"

__all__ = [
    "Converter", "ConversionError", "PixelResult", "EPSILON",
    "compare_all", "compare_paths",
    "ColorMatrix", "get_matrix", "MixedStandardError",
    "Standard", "get_standard",
    "SampleRange", "get_range", "FULL", "LIMITED",
    "ChromaLayout", "get_layout",
    "RangeTags", "Trace",
    "run_request", "run_file",
    "lossless_channels", "classify_roundtrip", "clipping_rules", "TOLERANCE",
    "__version__",
]

