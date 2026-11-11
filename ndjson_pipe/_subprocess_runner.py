"""Absolute-path runner for isolated (-I) subprocess invocations.

``python -I`` ignores PYTHONPATH and the caller's working directory, but the
directory of an explicitly named script is placed on ``sys.path``. Here that
directory is the package itself, so the project root (its parent) is inserted
explicitly before importing :mod:`ndjson_pipe`.
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ndjson_pipe.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
