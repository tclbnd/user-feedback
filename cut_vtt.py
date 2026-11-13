# -*- coding: utf-8 -*-
"""Command line entry point for the EDL driven WebVTT cutter.

Usage: python3 cut_vtt.py edl.json output.vtt cue-map.json
See vttcutter.py for the implementation and its exact timing rules.
"""
import sys

from vttcutter import main

if __name__ == "__main__":
    sys.exit(main())
