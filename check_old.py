"""Prove the harness catches the original overwrite-manifest bug.

Extracts HEAD:lake.py (the pre-fix implementation) into a scratch directory
next to a copy of the tests and runs the same interleavings against it.
"""
import pathlib
import shutil
import subprocess
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
SPECS = __import__("test_compaction").SPECS


def build_scratch():
    scratch = pathlib.Path(tempfile.mkdtemp(prefix="oldtests-"))
    old = subprocess.run(["git", "show", "HEAD:lake.py"], cwd=str(HERE),
                         capture_output=True, text=True, check=True).stdout
    # Only align the call signature so the harness can reach the actual
    # concurrency logic; the overwrite-manifest behaviour is left untouched.
    old = old.replace("def commit(self, name, crash):",
                      "def commit(self, name, crash=None):")
    old = old.replace("def read(self, version):",
                      "def read(self, version=None):")
    old = old.replace("def gc(self, crash):",
                      "def gc(self, crash=None):")
    (scratch / "lake.py").write_text(old)
    shutil.copy(HERE / "test_compaction.py", scratch / "test_compaction.py")
    return scratch


def main():
    scratch = build_scratch()
    env = {"PYTHONPYCACHEPREFIX": "/tmp/pyc"}
    code = (
        "import test_compaction as t\n"
        "failed = 0\n"
        "for i,s in enumerate(t.SPECS,1):\n"
        "    try:\n"
        "        t.run_spec(s,'spec%02d'%i)\n"
        "    except Exception as error:\n"
        "        failed += 1\n"
        "        print('DETECTED spec%02d: %r' % (i, error))\n"
        "print('old implementation failed %d/%d specs' % (failed, len(t.SPECS)))\n"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(scratch),
                          capture_output=True, text=True,
                          env={**__import__("os").environ, **env})
    print(proc.stdout)
    if proc.returncode != 0:
        print(proc.stderr)
    detected = proc.stdout.count("DETECTED")
    print("harness detected the regression in %d/%d interleavings" % (detected, len(SPECS)))
    return 0 if detected >= 10 else 1


if __name__ == "__main__":
    sys.exit(main())
