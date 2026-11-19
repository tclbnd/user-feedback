import unittest

from runsec import run


def db(policies, roles=None, table=None):
    return {
        "roles": roles or {"u": {}},
        "tables": {"t": table or {"owner": "o", "rls": True,
                                  "rows": [{"id": 1, "k": "a"}, {"id": 2, "k": "b"}]}},
        "policies": policies,
    }


def pol(name, command="all", kind="permissive", using=None, check=None, roles=None):
    return {"name": name, "table": "t", "command": command, "kind": kind,
            "roles": roles or ["public"], "using": using, "check": check}


U = {"role": "u", "user": "u"}


class CombinationTests(unittest.TestCase):
    def test_permissive_policies_are_ored(self):
        d = db([pol("p1", "select", using={"cmp": ["k", "=", "a"]}),
                pol("p2", "select", using={"cmp": ["k", "=", "b"]})])
        out = run(d, [{"as": U, "select": "t"}])
        self.assertEqual(out[0]["ids"], [1, 2])

    def test_restrictive_policies_and_with_permissive(self):
        d = db([pol("p1", "select", "permissive", using=True),
                pol("r1", "select", "restrictive", using={"cmp": ["k", "=", "a"]})])
        out = run(d, [{"as": U, "select": "t"}])
        self.assertEqual(out[0]["ids"], [1])

    def test_restrictive_alone_denies(self):
        d = db([pol("r1", "select", "restrictive", using=True)])
        out = run(d, [{"as": U, "select": "t"}])
        self.assertEqual(out[0]["ids"], [])

    def test_no_policy_denies_everything(self):
        out = run(db([]), [{"as": U, "select": "t"},
                           {"as": U, "delete": "t"},
                           {"as": U, "insert": "t", "row": {"id": 3, "k": "c"}}])
        self.assertEqual(out[0]["ids"], [])
        self.assertEqual(out[1]["rows"], 0)
        self.assertIn("error", out[2])


class BypassTests(unittest.TestCase):
    P = [pol("p1", "select", using={"cmp": ["k", "=", "a"]})]

    def test_owner_bypasses(self):
        out = run(db(self.P), [{"as": {"role": "o"}, "select": "t"}])
        self.assertEqual(out[0]["ids"], [1, 2])

    def test_force_applies_to_owner(self):
        d = db(self.P, table={"owner": "o", "rls": True, "force": True,
                              "rows": [{"id": 1, "k": "a"}, {"id": 2, "k": "b"}]})
        out = run(d, [{"as": {"role": "o"}, "select": "t"}])
        self.assertEqual(out[0]["ids"], [1])

    def test_bypassrls_and_superuser_bypass_even_with_force(self):
        roles = {"b": {"bypassrls": True}, "s": {"superuser": True}}
        table = {"owner": "o", "rls": True, "force": True,
                 "rows": [{"id": 1, "k": "a"}, {"id": 2, "k": "b"}]}
        out = run(db(self.P, roles=roles, table=table),
                  [{"as": {"role": "b"}, "select": "t"},
                   {"as": {"role": "s"}, "select": "t"}])
        self.assertEqual(out[0]["ids"], [1, 2])
        self.assertEqual(out[1]["ids"], [1, 2])


class CommandScopeTests(unittest.TestCase):
    def test_insert_uses_with_check_and_raises(self):
        d = db([pol("p1", "insert", check={"cmp": ["k", "=", "a"]})])
        out = run(d, [{"as": U, "insert": "t", "row": {"id": 3, "k": "a"}},
                      {"as": U, "insert": "t", "row": {"id": 4, "k": "z"}}])
        self.assertEqual(out[0]["rows"], 1)
        self.assertIn("error", out[1])
        self.assertEqual([r["id"] for r in d["tables"]["t"]["rows"]], [1, 2, 3])

    def test_check_falls_back_to_using(self):
        d = db([pol("p1", "all", using={"cmp": ["k", "=", "a"]})])
        out = run(d, [{"as": U, "insert": "t", "row": {"id": 3, "k": "a"}},
                      {"as": U, "insert": "t", "row": {"id": 4, "k": "z"}}])
        self.assertEqual(out[0]["rows"], 1)
        self.assertIn("error", out[1])

    def test_update_using_filters_check_validates(self):
        d = db([pol("p1", "update", using={"cmp": ["k", "=", "a"]},
                    check={"cmp": ["k", "=", "a"]})])
        # Row 2 fails the UPDATE USING, so it is silently untouched;
        # updating row 1 to k='z' violates WITH CHECK and raises.  The
        # second statement has no WHERE, so no SELECT policy is needed.
        out = run(d, [{"as": U, "update": "t", "where": {"cmp": ["k", "=", "b"]}, "set": {"k": "a"}},
                      {"as": U, "update": "t", "set": {"k": "z"}}])
        self.assertEqual(out[0]["rows"], 0)
        self.assertIn("error", out[1])

    def test_delete_uses_using_only(self):
        d = db([pol("p1", "delete", using={"cmp": ["k", "=", "a"]})])
        out = run(d, [{"as": U, "delete": "t"}])
        self.assertEqual(out[0]["rows"], 1)
        self.assertEqual([r["id"] for r in d["tables"]["t"]["rows"]], [2])


if __name__ == "__main__":
    unittest.main()
