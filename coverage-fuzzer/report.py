#!/usr/bin/env python3
"""report.py -- reproduce deduped crashes on a non-instrumented build.

Rebuilds the target WITHOUT coverage instrumentation (ASan is kept, which
SPEC.md explicitly allows: "sanitizer builds are fine") and re-runs every
deduped crash from crashes/.  Prints a table: crash id, dedup signature,
minimised size, and whether the crash still reproduces.

Python standard library + system clang only.
"""

import argparse
import json
import os
import re
import subprocess
import sys

ASAN_EXIT = 77
ASAN_ENV = (
    'detect_leaks=0:abort_on_error=0:symbolize=1:'
    'allocator_may_return_null=1:exitcode=%d' % ASAN_EXIT
)

ASAN_ERR_RE = re.compile(rb'ERROR: AddressSanitizer: ([\w-]+)')
FRAME0_RE = re.compile(rb'^\s*#0\s+0x[0-9a-fA-F]+\s+in\s+([\w.$]+)', re.M)


def build_plain(target, build_dir):
    """Compile without coverage instrumentation (ASan only)."""
    os.makedirs(build_dir, exist_ok=True)
    binary = os.path.join(build_dir, 'report_target')
    subprocess.run(
        ['clang', '-O0', '-g', '-fsanitize=address',
         os.path.abspath(target), '-o', binary],
        check=True)
    return binary


def run(binary, path, timeout=10):
    env = dict(os.environ)
    env['ASAN_OPTIONS'] = ASAN_ENV
    try:
        proc = subprocess.run(
            [binary, path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=timeout,
            env=env,
        )
        return proc.returncode, proc.stderr
    except subprocess.TimeoutExpired:
        return None, b''


def signature(rc, stderr):
    m = ASAN_ERR_RE.search(stderr)
    if m:
        err = m.group(1).decode('ascii', 'replace')
        f = FRAME0_RE.search(stderr)
        func = f.group(1).decode('ascii', 'replace') if f else '?'
        return 'asan:%s:%s' % (err, func)
    if rc is not None and rc < 0:
        return 'signal:%d' % (-rc)
    return 'exit:%s' % rc


def is_crash(rc):
    return rc is not None and rc not in (0, 2)


def load_crashes(crash_dir):
    """Return list of {id, signature, file, min_file, min_size}."""
    manifest_path = os.path.join(crash_dir, 'manifest.json')
    if os.path.exists(manifest_path):
        with open(manifest_path) as f:
            return json.load(f)['crashes']
    # fallback: scan directory (no recorded signatures)
    entries = []
    for name in sorted(os.listdir(crash_dir)):
        if name.endswith('.min') or name == 'manifest.json':
            continue
        path = os.path.join(crash_dir, name)
        if not os.path.isfile(path):
            continue
        min_path = path + '.min'
        entries.append({
            'id': name,
            'signature': None,
            'file': name,
            'min_file': name + '.min' if os.path.exists(min_path) else None,
            'min_size': (os.path.getsize(min_path)
                         if os.path.exists(min_path) else None),
        })
    return entries


def main():
    ap = argparse.ArgumentParser(description='reproduce deduped crashes')
    ap.add_argument('--target', default='json_parser.c')
    ap.add_argument('--crashes', default='crashes')
    ap.add_argument('--build-dir', default='build')
    args = ap.parse_args()

    entries = load_crashes(args.crashes)
    if not entries:
        print('no crashes found in %s -- run fuzzer.py first' % args.crashes)
        sys.exit(1)

    print('[*] building %s without coverage instrumentation (ASan only)'
          % args.target)
    binary = build_plain(args.target, args.build_dir)

    rows = []
    for e in entries:
        path = os.path.join(args.crashes, e['file'])
        rc, stderr = run(binary, path)
        crashed = is_crash(rc)
        sig = signature(rc, stderr) if crashed else None
        recorded = e.get('signature')
        rows.append({
            'id': e['id'],
            'signature': recorded or sig or '-',
            'min_size': e.get('min_size'),
            'still_crashes': crashed,
            'sig_match': (recorded is None or sig == recorded),
        })

    print('\n%-18s %-42s %-9s %-8s' % ('CRASH ID', 'DEDUP SIGNATURE',
                                       'MIN SIZE', 'CRASHES'))
    print('-' * 82)
    for r in rows:
        print('%-18s %-42s %-9s %-8s'
              % (r['id'], r['signature'],
                 r['min_size'] if r['min_size'] is not None else '-',
                 'YES' if r['still_crashes'] else 'no'))

    n_ok = sum(1 for r in rows if r['still_crashes'])
    n_sig = sum(1 for r in rows if r['sig_match'])
    print('-' * 82)
    print('summary: %d/%d deduped crashes still crash the plain build; '
          '%d/%d signatures match the fuzzing run'
          % (n_ok, len(rows), n_sig, len(rows)))
    if n_ok != len(rows):
        sys.exit(1)


if __name__ == '__main__':
    main()
