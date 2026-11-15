"""arfix 验收脚本:
1) <=40 样本损坏段逐样本误差 <=5%; 爆点检测 Precision>=0.95; 干净段零改动
2) 2 组块大小 x 3 组损坏密度复跑, 输出摘要(digest)一致
"""
import math, random, sys, hashlib
sys.path.insert(0, '.')
import arfix

SR = 48000.0
N = 4096
NEV = dict(low=6, mid=15, high=30)


def gen_signal(n, seed):
    rng = random.Random(seed)
    comps = [(440.0, 0.6), (1170.0, 0.3), (2530.0, 0.15)]
    ph = [rng.uniform(0, 6.2832) for _ in comps]
    x = []
    for t in range(n):
        v = sum(a * math.sin(2 * math.pi * f * t / SR + p)
                for (f, a), p in zip(comps, ph))
        v += 0.002 * rng.uniform(-1, 1)
        x.append(v)
    return x


def corrupt(x, density, seed):
    rng = random.Random(seed)
    n = len(x)
    y = list(x)
    peak = max(abs(v) for v in x)
    damaged = set()
    intervals = []
    placed = []
    tries = 0
    while len(intervals) < NEV[density] and tries < 20000:
        tries += 1
        if rng.random() < 0.55:
            L = rng.randint(5, 40)
            kind = 'zero'
        else:
            L = rng.randint(1, 3)
            kind = 'spike'
        s = rng.randint(60, n - 60 - L)
        if any(abs(s - p) < 60 for p in placed):
            continue
        placed.append(s)
        if kind == 'zero':
            for k in range(s, s + L):
                y[k] = 0.0
        else:
            for k in range(s, s + L):
                y[k] = rng.choice([-1, 1]) * rng.uniform(4, 8) * peak
        intervals.append((s, L))
        damaged.update(range(s, s + L))
    return y, damaged, intervals


def check1():
    print('=== check1: precision + repair error (3 densities x 2 seeds) ===')
    for density, gseed, cseed in [('low', 1, 7), ('mid', 1, 7), ('mid', 3, 19),
                                  ('high', 2, 5), ('high', 4, 23)]:
        ref = gen_signal(N, seed=gseed)
        y, D, iv = corrupt(ref, density, seed=cseed)
        peak = max(abs(v) for v in ref)
        out, repairs, stats, digest = arfix.process(list(y), 512)
        R = set()
        for s, L in repairs:
            R.update(range(s, s + L))
        tp = len(R & D)
        prec = tp / max(len(R), 1)
        rec = tp / max(len(D), 1)
        untouched = all(out[k] == y[k] for k in range(N) if k not in R)
        worst = 0.0
        for s, L in iv:
            if L <= 40:
                for k in range(s, s + L):
                    worst = max(worst, abs(out[k] - ref[k]) / peak)
        print('%-5s seed=%d/%d events=%2d det=%2d P=%.4f R=%.4f '
              'clean_untouched=%s err<=%.4f%%' % (
                  density, gseed, cseed, len(iv), len(repairs), prec, rec,
                  untouched, worst * 100))
        assert prec >= 0.95, 'precision < 0.95'
        assert untouched, 'clean segment modified'
        assert worst <= 0.05, 'error > 5%'
    print('PASS')


def check2():
    print('=== check2: 2 block sizes x 3 densities, digest consistent ===')
    for density, cseed in [('low', 11), ('mid', 22), ('high', 33)]:
        ref = gen_signal(N, seed=2)
        y, D, iv = corrupt(ref, density, seed=cseed)
        digs = []
        for block in (256, 1024):
            o, r, st, dg = arfix.process(list(y), block)
            full = hashlib.sha1(
                ','.join('%.6f' % v for v in o).encode()).hexdigest()
            digs.append(full)
        ok = digs[0] == digs[1]
        print('density=%-5s events=%2d  full-digest(b256)=%s  (b1024)=%s  '
              'consistent=%s'
              % (density, len(iv), digs[0][:12], digs[1][:12], ok))
        assert ok, 'block-size dependent output'
    print('PASS')


if __name__ == '__main__':
    check1()
    print()
    check2()
