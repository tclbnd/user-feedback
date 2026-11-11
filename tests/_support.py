"""Shared helpers for the recorder acceptance tests.

Fixtures are synthesized once per scenario into a temporary directory and
reused across test cases, so the whole suite only pays the ffmpeg encoding
cost a handful of times.  Every test works purely with local files and the
bundled ffmpeg executable.
"""

import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import fixtures  # noqa: E402
from mux import ffmpeg_executable  # noqa: E402

FIXTURE_ROOT = Path(os.environ.get(
    "RECORDER_TEST_FIXTURES",
    str(Path(__file__).resolve().parent / "_fixture_cache")))


def ensure_fixture(scenario: str) -> Path:
    """Generate (once) and return the capture directory for a scenario."""
    target = FIXTURE_ROOT / scenario
    if not (target / "capture.jsonl").exists():
        target.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        fixtures.SCENARIOS[scenario](target)
    return target


def run_recorder(capture: Path, output: Path, env_extra=None) -> int:
    env = dict(os.environ)
    env.setdefault("FFMPEG", str(ROOT / "bin" / "ffmpeg"))
    if env_extra:
        env.update(env_extra)
    return subprocess.call(
        [sys.executable, str(ROOT / "recorder.py"), str(capture), str(output)],
        env=env)


def decode_playlist(playlist: Path) -> subprocess.CompletedProcess:
    """Actually decode both tracks of a published playlist."""
    return subprocess.run(
        [ffmpeg_executable(), "-hide_banner", "-loglevel", "error",
         "-i", str(playlist), "-map", "0:v:0", "-map", "0:a:0",
         "-f", "null", "-"],
        capture_output=True)


class FixtureTestCase(unittest.TestCase):
    scenario = "sample"

    @classmethod
    def setUpClass(cls):
        cls.fixture = ensure_fixture(cls.scenario)
        cls.capture = cls.fixture / "capture.jsonl"

    def publish(self, name, env_extra=None):
        output = Path(self._testMethodName_dir()) / name
        output.parent.mkdir(parents=True, exist_ok=True)
        code = run_recorder(self.capture, output, env_extra)
        return code, output

    def _testMethodName_dir(self) -> str:
        base = FIXTURE_ROOT / "out" / type(self).__name__
        return str(base / self._testMethodName)
