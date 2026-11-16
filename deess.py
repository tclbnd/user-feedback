#!/usr/bin/env python3
"""Backwards-compatible entry point for the de-esser.

The engine lives in :mod:`deesser`; this script keeps the documented command
line::

    python3 deess.py input.json output.json

and accepts both job shapes:

* legacy frame jobs ``{"frames":[{"t","low","high"}...],"threshold":T,
  "ratio":R,"attack":A,"release":L}`` (original contract, fixed envelope and
  ramped bypass), and
* streaming jobs ``{"audio":[[s]...],"sample_rate":48000,"params":{...}}``
  which run the signal-domain band split.

``python3 deesser.py`` is equivalent.
"""

import sys

from deesser import main

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
