import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

import cueinfo

EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


def run(args):
    out = io.StringIO()
    with redirect_stdout(out):
        status = cueinfo.main(args)
    return status, json.loads(out.getvalue())


class CliTests(unittest.TestCase):
    def test_harbour(self):
        status, data = run([str(EXAMPLES / "harbour.cue"),
                            "--length", "Harbour Lights.wav=105840000"])
        self.assertEqual(status, 0)
        self.assertEqual(data["title"], "Harbour Lights")
        self.assertEqual(len(data["tracks"]), 4)
        self.assertEqual(data["tracks"][0]["title"], "Low Tide, High Hopes")
        self.assertEqual(data["tracks"][3]["end"], 105840000)

    def test_length_forms(self):
        for args in (["--length", "Harbour Lights.wav=105840000"],
                     ["--length=Harbour Lights.wav=105840000"]):
            status, data = run([str(EXAMPLES / "harbour.cue")] + args)
            self.assertEqual(status, 0)
            self.assertEqual(data["tracks"][3]["end"], 105840000)

    def test_bad_sheet(self):
        sheet = 'FILE "a.wav" WAVE\n  TRACK 01 AUDIO\n    INDEX 01 00:00:99\n'
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "bad.cue"
            path.write_text(sheet, encoding="utf-8")
            status, data = run([str(path)])
        self.assertEqual(status, 1)
        self.assertIn("error", data)

    def test_missing_sheet(self):
        status, data = run([str(EXAMPLES / "nope.cue")])
        self.assertEqual(status, 1)
        self.assertIn("error", data)


if __name__ == "__main__":
    unittest.main()
