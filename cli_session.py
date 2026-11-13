"""End-to-end CLI transcript: exercises every action and prints exit codes."""
import json
import os
import pathlib
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
ROOT = pathlib.Path(tempfile.mkdtemp(prefix="cli-"))


def data(name, payload):
    path = ROOT / (name + ".in")
    path.write_text(json.dumps(payload))
    return str(path)


def run(*args):
    proc = subprocess.run(
        [sys.executable, str(HERE / "lake.py"), str(ROOT), *args],
        capture_output=True, text=True,
        env={**os.environ, "PYTHONPYCACHEPREFIX": "/tmp/pyc"})
    out = proc.stdout.strip() or proc.stderr.strip().splitlines()[-1:]
    print("$ lake %s -> rc=%d %s" % (" ".join(args), proc.returncode, out))
    return proc


def main():
    a = data("a", [{"id": "a", "value": 10}, {"id": "b", "value": 20}])
    b = data("b", [{"id": "c", "value": 30}, {"id": "d", "value": 40}])
    a2 = data("a2", [{"id": "e", "value": 50}])
    del_a = data("del-a", ["a"])
    del_z = data("del-z", ["z"])

    print("== two appends ==")
    run("append", a)
    run("append", b)
    print("== prepare is idempotent ==")
    run("prepare", "p")
    run("prepare", "p")
    print("== old snapshot readable ==")
    run("read", "--version", "2")
    print("== delete a selected row then commit -> conflict ==")
    run("delete", del_a)
    run("commit", "p")
    print("== fresh plan: delete unselected id + append then commit -> rebase ==")
    run("prepare", "q")
    run("delete", del_z)
    run("append", a2)
    run("commit", "q")
    run("read")
    print("== pin then gc keeps the old snapshot ==")
    run("pin", "5")
    run("gc")
    run("read", "--version", "5")
    print("== gc crash before unlink, then recover ==")
    run("append", a2)
    run("gc", "--crash", "before-unlink")
    run("gc")
    run("state")
    print("== commit crashes, then recovers ==")
    run("append", b)
    run("prepare", "r")
    run("append", a2)
    run("commit", "r", "--crash", "after-data")
    run("commit", "r")
    run("commit", "r", "--crash", "after-publish")
    run("commit", "r")
    run("state")
    return 0


if __name__ == "__main__":
    sys.exit(main())
