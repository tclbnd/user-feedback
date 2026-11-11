import shutil
import subprocess
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from fixtures import generate
from mux import ffmpeg_executable


def _ffmpeg_available() -> bool:
    candidate = ffmpeg_executable()
    try:
        subprocess.run([candidate, "-version"], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, check=False)
        return True
    except OSError:
        return False


class NormalCaptureTest(unittest.TestCase):
    @unittest.skipUnless(_ffmpeg_available(),
                         "FFmpeg is required for fixture generation and decode")
    def test_cli_packages_and_decodes_both_tracks(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            capture = generate(root / "source")
            published = root / "published"
            cli = Path(__file__).resolve().parents[1] / "recorder.py"
            subprocess.run([sys.executable, str(cli), str(capture), str(published)], check=True)
            index = published / "index.m3u8"
            playlist = index.read_text()
            self.assertIn("#EXT-X-PLAYLIST-TYPE:EVENT", playlist)
            self.assertIn("#EXT-X-MAP:", playlist)
            self.assertIn("#EXT-X-ENDLIST", playlist)
            self.assertGreaterEqual(playlist.count("#EXTINF"), 2)
            subprocess.run([
                ffmpeg_executable(), "-hide_banner", "-loglevel", "error", "-i", str(index),
                "-map", "0:v:0", "-map", "0:a:0", "-f", "null", "-",
            ], check=True)
