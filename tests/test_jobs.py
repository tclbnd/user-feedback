import unittest

from rcte.bom import mrp_requirements
from rcte.export import org_rows, people_under, to_csv
from rcte.graph import Graph

ORG = Graph([{"id": "boss", "title": "CEO"}, {"id": "a"}, {"id": "b"}, {"id": "c"}],
            [{"src": "boss", "dst": "a", "kind": "manages"}, {"src": "boss", "dst": "b", "kind": "manages"},
             {"src": "b", "dst": "c", "kind": "manages"}])
CHAIN = Graph([{"id": x} for x in ("kit", "box", "screw")],
              [{"src": "kit", "dst": "box", "qty": 2}, {"src": "box", "dst": "screw", "qty": 6}])


class JobTests(unittest.TestCase):
    def test_export_is_depth_first(self):
        rows = list(org_rows(ORG))
        self.assertEqual([r["id"] for r in rows], ["boss", "a", "b", "c"])
        self.assertTrue(to_csv(rows).startswith("id,depth,path,is_cycle\n"))

    def test_people_under(self):
        self.assertEqual(sorted(people_under(ORG, "boss")), ["a", "b", "c"])

    def test_mrp_chain(self):
        out = mrp_requirements(CHAIN, "kit", 3)
        self.assertEqual(out, {"requirements": {"box": 6, "screw": 36}, "loops": []})


if __name__ == "__main__":
    unittest.main()
