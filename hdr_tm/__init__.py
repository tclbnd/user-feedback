"""HDR zone metering + edge-aware local tone mapping (standard library only).

Public API
----------
run(req, base_dir=".") -> dict      # req is the parsed JSON request
run_file(req_path, out_path)        # CLI helper
"""
from .pipeline import run, run_file, target_display, TARGET_DISPLAY

__all__ = ["run", "run_file", "target_display", "TARGET_DISPLAY"]
