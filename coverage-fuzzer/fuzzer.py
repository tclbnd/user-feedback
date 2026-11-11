#!/usr/bin/env python3
"""fuzzer.py -- coverage-guided greybox fuzzer.

Pipeline (see SPEC.md):
  1. compile the target with clang edge-coverage instrumentation
     (-fsanitize-coverage=trace-pc-guard) plus a small runtime (embedded
     below) that records an edge bitmap and dumps it to $COV_OUT at exit;
     ASan is enabled so memory errors become observable, symbolised crashes;
  2. execute corpus inputs, collecting an edge-coverage bitmap per run;
  3. seed scheduling prefers inputs that discovered new edges;
  4. mutation: bit flips, byte insert/delete/set, byte splice, dictionary
     tokens ({}[]":, true false null \\u) and slice duplication;
  5. crash collection: dedup by top-of-stack signature, keep the crashing
     input in crashes/ plus a minimised one-line variant per crash.

Python standard library + system clang only.
"""

import argparse
import hashlib
import json
import os
import random
import re
import subprocess
import sys
import tempfile
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

MAP_SIZE = 65536
ASAN_EXIT = 77
MAX_INPUT = 8192
RUN_TIMEOUT = 10  # seconds per execution

DICTIONARY = [
    b'{', b'}', b'[', b']', b'"', b':', b',',
    b'true', b'false', b'null', b'\\u',
]

ASAN_ENV = (
    'detect_leaks=0:abort_on_error=0:symbolize=1:'
    'allocator_may_return_null=1:exitcode=%d' % ASAN_EXIT
)

# Runtime linked with the instrumented target.  It is compiled WITHOUT
# coverage flags (instrumenting the callbacks would recurse forever).
COV_RUNTIME_C = r'''
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>

#define MAP_SIZE 65536u
static uint8_t cov_map[MAP_SIZE];
static uint32_t num_guards;

void __sanitizer_cov_trace_pc_guard_init(uint32_t *start, uint32_t *stop) {
    if (start == stop || *start) return;
    for (uint32_t *x = start; x < stop; x++)
        *x = ++num_guards;
}

void __sanitizer_cov_trace_pc_guard(uint32_t *guard) {
    if (!*guard) return;
    cov_map[*guard % MAP_SIZE] = 1;
}

__attribute__((destructor))
static void cov_dump(void) {
    const char *out = getenv("COV_OUT");
    if (!out || !*out) return;
    FILE *f = fopen(out, "wb");
    if (!f) return;
    fwrite(cov_map, 1, MAP_SIZE, f);
    fclose(f);
}
'''

ASAN_ERR_RE = re.compile(rb'ERROR: AddressSanitizer: ([\w-]+)')
FRAME0_RE = re.compile(rb'^\s*#0\s+0x[0-9a-fA-F]+\s+in\s+([\w.$]+)', re.M)


class Result:
    __slots__ = ('rc', 'stderr', 'cov', 'timed_out')

    def __init__(self, rc, stderr, cov, timed_out):
        self.rc = rc
        self.stderr = stderr
        self.cov = cov
        self.timed_out = timed_out

    def is_crash(self):
        # Normal target exits: 0 (valid JSON), 2 (syntax error).
        return (not self.timed_out) and self.rc is not None and self.rc not in (0, 2)


class Runner:
    """Executes the instrumented binary on inputs written to temp files."""

    def __init__(self, binary, workdir, timeout=RUN_TIMEOUT):
        self.binary = binary
        self.workdir = workdir
        self.timeout = timeout
        os.makedirs(workdir, exist_ok=True)

    def run(self, data):
        fd, inpath = tempfile.mkstemp(prefix='case_', dir=self.workdir)
        covpath = inpath + '.cov'
        try:
            with os.fdopen(fd, 'wb') as f:
                f.write(data)
            env = dict(os.environ)
            env['COV_OUT'] = covpath
            env['ASAN_OPTIONS'] = ASAN_ENV
            try:
                proc = subprocess.run(
                    [self.binary, inpath],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    timeout=self.timeout,
                    env=env,
                )
                rc, stderr, timed_out = proc.returncode, proc.stderr, False
            except subprocess.TimeoutExpired:
                rc, stderr, timed_out = None, b'', True
            cov = None
            if os.path.exists(covpath):
                with open(covpath, 'rb') as f:
                    cov = f.read()
            return Result(rc, stderr, cov, timed_out)
        finally:
            for p in (inpath, covpath):
                try:
                    os.unlink(p)
                except OSError:
                    pass


def crash_signature(res):
    """Top-of-stack signature: ASan error type + top user frame function."""
    m = ASAN_ERR_RE.search(res.stderr)
    if m:
        err = m.group(1).decode('ascii', 'replace')
        f = FRAME0_RE.search(res.stderr)
        func = f.group(1).decode('ascii', 'replace') if f else '?'
        return 'asan:%s:%s' % (err, func)
    if res.rc is not None and res.rc < 0:
        return 'signal:%d' % (-res.rc)
    return 'exit:%s' % res.rc


def compile_instrumented(target, build_dir):
    """Build target with edge coverage + ASan; returns binary path.

    Compiled at -O0 on purpose: at -O1 the compiler dead-store-eliminates
    the target's buggy writes (buf/ctxstack are never read), which hides
    the very memory errors we are hunting."""
    os.makedirs(build_dir, exist_ok=True)
    src = os.path.abspath(target)
    rt_c = os.path.join(build_dir, 'cov_runtime.c')
    rt_o = os.path.join(build_dir, 'cov_runtime.o')
    binary = os.path.join(build_dir, 'fuzz_target')
    with open(rt_c, 'w') as f:
        f.write(COV_RUNTIME_C)
    subprocess.run(
        ['clang', '-O0', '-c', rt_c, '-o', rt_o], check=True)
    subprocess.run(
        ['clang', '-O0', '-g', '-fsanitize=address',
         '-fsanitize-coverage=trace-pc-guard', src, rt_o, '-o', binary],
        check=True)
    return binary


# ---------------------------------------------------------------- mutation

def mutate(rng, base, corpus):
    data = bytearray(base)
    for _ in range(rng.randint(1, 3)):
        op = rng.randrange(8)
        if op == 0 and data:  # bit flip
            i = rng.randrange(len(data))
            data[i] ^= 1 << rng.randrange(8)
        elif op == 1:  # insert random byte
            i = rng.randrange(len(data) + 1)
            data[i:i] = bytes([rng.randrange(256)])
        elif op == 2 and data:  # delete byte
            i = rng.randrange(len(data))
            del data[i]
        elif op == 3 and data:  # set byte
            i = rng.randrange(len(data))
            data[i] = rng.randrange(256)
        elif op == 4 and len(corpus) > 1 and data:  # splice two inputs
            other = rng.choice(corpus)
            if other:
                x = rng.randrange(len(data) + 1)
                y = rng.randrange(len(other) + 1)
                data = bytearray(data[:x]) + bytearray(other[y:])
        elif op == 5:  # dictionary token insert
            i = rng.randrange(len(data) + 1)
            data[i:i] = rng.choice(DICTIONARY)
        elif op == 6 and data:  # duplicate a slice (builds depth / length)
            i = rng.randrange(len(data))
            j = min(len(data), i + rng.randint(1, 64))
            copies = rng.randint(2, 160)
            data[j:j] = bytes(data[i:j]) * copies
        elif op == 7 and data:  # delete a slice
            i = rng.randrange(len(data))
            j = min(len(data), i + rng.randint(1, 64))
            del data[i:j]
        if len(data) > MAX_INPUT:
            del data[MAX_INPUT:]
    return bytes(data)


# ---------------------------------------------------------------- minimise

def minimize(runner, data, sig, max_execs=400, deadline=20.0):
    """Greedy chunk-deletion minimiser; keeps the same crash signature."""
    t0 = time.time()
    execs = [0]

    def crashes(d):
        if execs[0] >= max_execs or time.time() - t0 > deadline:
            return False
        execs[0] += 1
        r = runner.run(d)
        return r.is_crash() and crash_signature(r) == sig

    n = 2
    while len(data) >= 2 and execs[0] < max_execs and time.time() - t0 < deadline:
        chunk = max(1, len(data) // n)
        changed = False
        i = 0
        while i < len(data) and execs[0] < max_execs and time.time() - t0 < deadline:
            cand = data[:i] + data[i + chunk:]
            if cand and crashes(cand):
                data = cand
                changed = True
            else:
                i += chunk
        if not changed:
            if chunk == 1:
                break
            n = min(len(data), n * 2)
        else:
            n = 2
    # one-line variant: drop newlines, or blank them (inside a string
    # literal a newline is significant, so try a space as well)
    for nl in (b'\n', b'\r'):
        for repl in (b'', b' '):
            if nl in data:
                cand = data.replace(nl, repl)
                if cand and crashes(cand):
                    data = cand
                    break
    return data


# ------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description='coverage-guided greybox fuzzer')
    ap.add_argument('--target', required=True, help='C source file to fuzz')
    ap.add_argument('--seeds', required=True, help='seed corpus directory')
    ap.add_argument('--time', type=float, default=300, help='fuzzing budget (s)')
    ap.add_argument('--out', default='crashes', help='crash output directory')
    ap.add_argument('--build-dir', default='build', help='build/work directory')
    ap.add_argument('--jobs', type=int,
                    default=max(2, min(8, (os.cpu_count() or 2))))
    args = ap.parse_args()

    rng = random.Random()
    os.makedirs(args.out, exist_ok=True)
    workdir = os.path.join(args.build_dir, 'work')

    print('[*] compiling %s with edge coverage + ASan' % args.target)
    binary = compile_instrumented(args.target, args.build_dir)
    runner = Runner(binary, workdir)

    # --- load seeds, build initial corpus -------------------------------
    seeds = []
    for name in sorted(os.listdir(args.seeds)):
        path = os.path.join(args.seeds, name)
        if os.path.isfile(path):
            with open(path, 'rb') as f:
                seeds.append(f.read())
    if not seeds:
        seeds = [b'{}', b'[]', b'1', b'"a"', b'true']

    global_cov = bytearray(MAP_SIZE)
    corpus = []          # list of bytes; entries all produced coverage
    recent = []          # most recent new-edge finds (scheduling favourites)
    crashes = []         # manifest entries
    seen_sigs = set()
    stats = {'execs': 0, 'timeouts': 0}
    t_start = time.time()
    t_end = t_start + args.time

    def note_coverage(res):
        """Merge coverage; return number of newly seen edges."""
        if not res.cov:
            return 0
        new = 0
        for i, b in enumerate(res.cov):
            if b and not global_cov[i]:
                global_cov[i] = 1
                new += 1
        return new

    def edges_total():
        return sum(global_cov)

    def save_manifest():
        manifest = {
            'target': os.path.abspath(args.target),
            'crashes': crashes,
        }
        with open(os.path.join(args.out, 'manifest.json'), 'w') as f:
            json.dump(manifest, f, indent=2)

    def handle_crash(data, res):
        sig = crash_signature(res)
        if sig in seen_sigs:
            return
        seen_sigs.add(sig)
        cid = 'crash_%02d_%s' % (
            len(crashes) + 1, hashlib.sha1(sig.encode()).hexdigest()[:6])
        path = os.path.join(args.out, cid)
        with open(path, 'wb') as f:
            f.write(data)
        print('\n[!] %s  signature=%s  size=%d  -> minimising'
              % (cid, sig, len(data)))
        min_data = minimize(runner, data, sig)
        min_path = os.path.join(args.out, cid + '.min')
        with open(min_path, 'wb') as f:
            f.write(min_data)
        crashes.append({
            'id': cid,
            'signature': sig,
            'file': os.path.basename(path),
            'min_file': os.path.basename(min_path),
            'size': len(data),
            'min_size': len(min_data),
        })
        save_manifest()
        print('[!] %s  minimised %d -> %d bytes' % (cid, len(data), len(min_data)))

    def handle_result(data, res):
        stats['execs'] += 1
        if res.timed_out:
            stats['timeouts'] += 1
            return
        if res.is_crash():
            handle_crash(data, res)
            return
        if note_coverage(res) > 0:
            corpus.append(data)
            recent.append(data)
            del recent[:-32]

    # --- calibrate seeds --------------------------------------------------
    print('[*] calibrating %d seeds' % len(seeds))
    for s in seeds:
        res = runner.run(s)
        handle_result(s, res)
        if s not in corpus:
            corpus.append(s)  # keep every seed in the gene pool
    print('[*] initial coverage: %d edges, corpus=%d' % (edges_total(), len(corpus)))

    # --- fuzz loop ----------------------------------------------------------
    def pick_seed():
        if recent and rng.random() < 0.5:
            return rng.choice(recent)
        return rng.choice(corpus)

    print('[*] fuzzing for %ds with %d workers' % (args.time, args.jobs))
    last_report = time.time()
    with ThreadPoolExecutor(max_workers=args.jobs) as ex:
        pending = {}  # future -> input data
        while time.time() < t_end or pending:
            while len(pending) < args.jobs * 2 and time.time() < t_end:
                data = mutate(rng, pick_seed(), corpus)
                pending[ex.submit(runner.run, data)] = data
            done, _ = wait(list(pending), timeout=0.5,
                           return_when=FIRST_COMPLETED)
            for fut in done:
                data = pending.pop(fut)
                handle_result(data, fut.result())
            now = time.time()
            if now - last_report >= 5:
                last_report = now
                print('[*] t=%5.0fs execs=%-7d corpus=%-4d edges=%-4d '
                      'crashes=%d (sigs=%d) timeouts=%d'
                      % (now - t_start, stats['execs'], len(corpus),
                         edges_total(), len(crashes), len(seen_sigs),
                         stats['timeouts']))

    save_manifest()

    # --- summary ----------------------------------------------------------
    print('\n========== fuzzing summary ==========')
    print('target      : %s' % os.path.abspath(args.target))
    print('executions  : %d (%d timeouts)' % (stats['execs'], stats['timeouts']))
    print('corpus      : %d inputs, %d edges covered'
          % (len(corpus), edges_total()))
    print('crashes     : %d distinct signatures' % len(crashes))
    for c in crashes:
        print('  %-16s sig=%-45s size=%-6d min=%d'
              % (c['id'], c['signature'], c['size'], c['min_size']))
    if len(crashes) >= 2:
        print('ACCEPTANCE: >= 2 distinct crash signatures found: OK')
    else:
        print('ACCEPTANCE: only %d distinct crash signature(s): FAILED'
              % len(crashes))
        sys.exit(1)


if __name__ == '__main__':
    main()
