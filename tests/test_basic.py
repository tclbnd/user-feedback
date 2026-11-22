import unittest

from nsfkit.nsf import parse

from rips import auth, info, nsf, nsfe


class BasicTests(unittest.TestCase):
    def test_nsf_header(self):
        out = parse(nsf(songs=8, title="Overworld"))
        self.assertEqual((out["format"], out["songs"], out["title"], out["load"]), ("NSF", 8, "Overworld", "8000"))

    def test_nsfe_auth(self):
        out = parse(nsfe(info(songs=4), auth("Racer", "Team")))
        self.assertEqual((out["format"], out["title"], out["artist"]), ("NSFe", "Racer", "Team"))
