#!/usr/bin/env python3
"""
cbf_acceptance.py — 计数布隆过滤器验收 harness（针对修复后的 bloom.py）

覆盖验收项：
1. 100k 混合负载：撤回后假阴性 = 0；在线估计假阳性率与实测误差 <= 20%。
2. 2 组容量 x 2 个随机种子复跑：终态计数摘要（digest）一致；操作序重放
   终态一致（进程内重放 + 通过 CLI 子进程重放双重验证）。

用法：
    python3 cbf_acceptance.py test     # 单元测试
    python3 cbf_acceptance.py accept   # 验收矩阵
    python3 cbf_acceptance.py run --buckets 1000000 --k 7 --seed 11 \
        --ops 100000 --log ops.jsonl   # 单次运行 + 逐操作日志
"""
import argparse
import json
import math
import os
import random
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import bloom  # noqa: E402  被验收对象：修复后的 bloom.py

HERE = os.path.dirname(os.path.abspath(__file__))


# ---------------------------------------------------------------------------
# 混合负载生成与执行
# ---------------------------------------------------------------------------
def gen_workload(seed, num_ops, universe):
    """生成确定性混合负载，并记录每个操作的地面真值标签。

    操作配比：ins 50% / del 20%（仅撤回当前存在的键，合法撤回）
              query_present 10% / query_absent 20%。
    ins 抽到已存在键时降级为 query_present，保证每个存在的键只被插入
    一次 —— 合法撤回序列下 CBF 结构上不存在假阴性。
    """
    rng = random.Random(seed)
    ops = []          # 发给 bloom.run 的操作
    labels = []       # 与 ops 对齐: ('ins'|'del'|'qp'|'qa', key)
    present = set()
    present_list = []
    pos = {}

    for _ in range(num_ops):
        r = rng.random()
        if r < 0.50:
            key = 'K%d' % rng.randrange(universe)
            if key in present:
                ops.append({'op': 'query', 'k': key})
                labels.append(('qp', key))
            else:
                ops.append({'op': 'ins', 'k': key})
                labels.append(('ins', key))
                pos[key] = len(present_list)
                present_list.append(key)
                present.add(key)
        elif r < 0.70:
            if not present_list:
                key = 'K%d' % rng.randrange(universe)
                ops.append({'op': 'ins', 'k': key})
                labels.append(('ins', key))
                pos[key] = len(present_list)
                present_list.append(key)
                present.add(key)
                continue
            idx = rng.randrange(len(present_list))
            key = present_list[idx]
            ops.append({'op': 'del', 'k': key})
            labels.append(('del', key))
            last = present_list.pop()
            if last != key:
                present_list[pos[key]] = last
                pos[last] = pos[key]
            del pos[key]
            present.discard(key)
        elif r < 0.80 and present_list:
            key = present_list[rng.randrange(len(present_list))]
            ops.append({'op': 'query', 'k': key})
            labels.append(('qp', key))
        else:
            while True:
                key = 'K%d' % rng.randrange(universe)
                if key not in present:
                    break
            ops.append({'op': 'query', 'k': key})
            labels.append(('qa', key))
    return ops, labels


def execute(buckets, k, seed, num_ops=100_000, universe=150_000,
            warmup=20_000, log_path=None):
    """执行混合负载，测量 FP/FN，并与在线估计对比。"""
    ops, labels = gen_workload(seed, num_ops, universe)
    req = {'buckets': buckets, 'k': k, 'seed': seed, 'ops': ops,
           'trace_estimates': True}
    res = bloom.run(req)

    # 将 query 判定与标签对齐（verdicts 仅含 query 操作）
    fn_events = fp_events = 0
    absent_q = 0
    est_samples = []
    q_iter = iter(res['verdicts'])
    log_f = open(log_path, 'w', encoding='utf-8') if log_path else None
    for i, (kind, key) in enumerate(labels):
        op = ops[i]['op']
        if op == 'query':
            verdict = next(q_iter)
            if kind == 'qp':
                if not verdict:
                    fn_events += 1          # 存在的键被判不存在 = 假阴性
                v = 'QUERY_HIT' if verdict else 'QUERY_MISS_FN'
            else:
                if i >= warmup:
                    absent_q += 1
                    est_samples.append(res['fp_trace'][i])
                    if verdict:
                        fp_events += 1      # 不存在的键被判存在 = 假阳性
                v = 'QUERY_HIT_FP' if verdict else 'QUERY_MISS'
        elif op == 'ins':
            v = 'INSERTED'
        else:
            v = 'DELETED'
        if log_f:
            log_f.write(json.dumps({
                'i': i, 'op': op, 'key': key, 'verdict': v,
                'fp_est': round(res['fp_trace'][i], 6),
            }) + '\n')
    if log_f:
        log_f.close()

    measured_fpr = fp_events / absent_q if absent_q else 0.0
    mean_est = sum(est_samples) / len(est_samples) if est_samples else 0.0
    rel_err = (abs(mean_est - measured_fpr) / measured_fpr
               if measured_fpr > 0 else 0.0)
    return {
        'buckets': buckets, 'k': k, 'seed': seed, 'num_ops': num_ops,
        'fn_events': fn_events, 'fp_events': fp_events,
        'absent_queries': absent_q,
        'measured_fpr': measured_fpr,
        'mean_online_est_fpr': mean_est,
        'fpr_rel_err': rel_err,
        'warn': res['warn'],
        'counts': res['counts'],
        'digest': res['digest'],
        'req': req,   # 供重放/CLI 复跑
    }


def replay_digest(req):
    """操作序重放：同一请求重放，终态摘要必须一致。"""
    return bloom.run(req)['digest']


def cli_digest(req):
    """通过 CLI 契约（python3 bloom.py in.json out.json）重放。"""
    with tempfile.TemporaryDirectory(dir=HERE) as td:
        fi = os.path.join(td, 'in.json')
        fo = os.path.join(td, 'out.json')
        with open(fi, 'w') as f:
            json.dump(req, f)
        subprocess.run([sys.executable, os.path.join(HERE, 'bloom.py'),
                        fi, fo], check=True)
        with open(fo) as f:
            return json.load(f)['digest']


# ---------------------------------------------------------------------------
# 单元测试
# ---------------------------------------------------------------------------
def unit_tests():
    print('== 单元测试 ==')

    # T1 基本插入/查询/撤回，撤回后假阴性为 0
    ops = ([{'op': 'ins', 'k': k} for k in ('a', 'b', 'c', 'd')]
           + [{'op': 'del', 'k': 'a'}]
           + [{'op': 'query', 'k': k} for k in ('a', 'b', 'c', 'd')])
    r = bloom.run({'buckets': 4096, 'k': 4, 'seed': 1, 'ops': ops})
    assert r['verdicts'] == [False, True, True, True], r['verdicts']
    print('T1 基本插入/撤回/查询，撤回后 FN=0 通过')

    # T2 撤回不存在的键被拒绝，计数器完全不变（防下溢、不污染其他键）
    ops = [{'op': 'ins', 'k': 'key-%d' % i} for i in range(50)]
    base = bloom.run({'buckets': 4096, 'k': 4, 'seed': 2, 'ops': ops})
    ghost = 0
    while bloom.run({'buckets': 4096, 'k': 4, 'seed': 2,
                     'ops': ops + [{'op': 'query',
                                    'k': 'ghost-%d' % ghost}]}
                     )['verdicts'][0]:
        ghost += 1
    r = bloom.run({'buckets': 4096, 'k': 4, 'seed': 2,
                   'ops': ops + [{'op': 'del', 'k': 'ghost-%d' % ghost}]})
    assert r['counts']['rejected_deletes'] == 1
    assert r['digest'] == base['digest'], '被拒绝的撤回改变了计数状态'
    assert r['counts']['bucket_sum'] == base['counts']['bucket_sum']
    print('T2 不存在键撤回被拒绝且计数器不变 通过')

    # T3 随机压力（含对任意键的撤回尝试）：无计数下溢
    rng = random.Random(99)
    ops = []
    for _ in range(30_000):
        key = 'u%d' % rng.randrange(5000)
        ops.append({'op': 'ins' if rng.random() < 0.55 else 'del', 'k': key})
    r = bloom.run({'buckets': 8192, 'k': 4, 'seed': 3, 'ops': ops})
    assert r['counts']['min_counter'] >= 0, '计数下溢'
    assert r['counts']['current_items'] >= 0
    print('T3 3 万随机操作无计数下溢 通过 (max=%d, rejected=%d)'
          % (r['counts']['max_counter'], r['counts']['rejected_deletes']))

    # T4 重放一致性：同请求重放 + CLI 子进程重放，终态摘要一致
    rng = random.Random(42)
    ops = []
    for _ in range(20_000):
        key = 'r%d' % rng.randrange(8000)
        ops.append({'op': 'ins' if rng.random() < 0.6 else 'del', 'k': key})
    req = {'buckets': 16384, 'k': 5, 'seed': 7, 'ops': ops}
    d1 = bloom.run(req)['digest']
    d2 = replay_digest(req)
    d3 = cli_digest(req)
    assert d1 == d2 == d3, (d1, d2, d3)
    print('T4 重放/CLI 复跑终态摘要一致 通过 (digest=%s…)' % d1[:16])

    # T5 在线 FPR 估计（桶占用校准）与实测误差 <= 20%
    rng = random.Random(7)
    ops = [{'op': 'ins', 'k': 'e%d' % i} for i in range(6000)]
    probes = []
    while len(probes) < 20_000:
        key = 'x%d' % rng.randrange(10 ** 9)
        probes.append({'op': 'query', 'k': key})
    r = bloom.run({'buckets': 100_000, 'k': 7, 'seed': 5,
                   'ops': ops + probes})
    measured = sum(r['verdicts']) / len(r['verdicts'])
    est = r['fp_estimate']
    err = abs(est - measured) / measured if measured else 0.0
    print('T5 在线估计FPR=%.5f 实测FPR=%.5f 相对误差=%.2f%%'
          % (est, measured, err * 100))
    assert err <= 0.20, '在线估计与实测误差超过 20%'
    print('T5 在线 FPR 估计校准 通过')

    # T6 接近饱和（非空桶占用 > 85%）预警
    ops = [{'op': 'ins', 'k': 's%d' % i} for i in range(300)]
    r = bloom.run({'buckets': 512, 'k': 4, 'seed': 6, 'ops': ops})
    occ = r['counts']['nonzero_buckets'] / 512
    assert occ > 0.85 and r['warn'] is True, (occ, r['warn'])
    r2 = bloom.run({'buckets': 1 << 20, 'k': 7, 'seed': 6, 'ops': ops})
    assert r2['warn'] is False
    print('T6 接近饱和预警（占用 %.1f%% > 85%% → warn）通过' % (occ * 100))

    print('全部单元测试通过\n')


# ---------------------------------------------------------------------------
# 验收矩阵：2 组容量 x 2 个种子 x 复跑 2 次
# ---------------------------------------------------------------------------
def acceptance():
    print('== 验收测试：100k 混合负载 ==')
    # 配置 A：健康容量（FPR≈0，不预警）；配置 B：紧张容量（占用>85% 触发
    # 饱和预警，FPR≈0.34，FP 样本充足使 20% 误差判据有统计意义）
    configs = [
        {'buckets': 1_000_000, 'k': 7, 'seed': 11},
        {'buckets': 1_000_000, 'k': 7, 'seed': 22},
        {'buckets': 90_000, 'k': 7, 'seed': 11},
        {'buckets': 90_000, 'k': 7, 'seed': 22},
    ]

    def fpr_ok(r):
        # 实测为 0 的低负载情形：要求在线估计同样接近 0（<0.1%）
        if r['measured_fpr'] == 0:
            return r['mean_online_est_fpr'] < 1e-3
        return r['fpr_rel_err'] <= 0.20

    all_ok = True
    for cfg in configs:
        runs = [execute(cfg['buckets'], cfg['k'], cfg['seed'])
                for _ in range(2)]
        a = runs[0]
        det_ok = runs[0]['digest'] == runs[1]['digest']       # 复跑确定
        replay_ok = replay_digest(a['req']) == a['digest']    # 进程内重放
        cli_ok = cli_digest(a['req']) == a['digest']          # CLI 重放
        fn_ok = all(r['fn_events'] == 0 for r in runs)
        fp_ok = all(fpr_ok(r) for r in runs)
        ok = det_ok and replay_ok and cli_ok and fn_ok and fp_ok
        all_ok &= ok
        print('B=%-8d k=%d seed=%-3d | FN=%d %s | 实测FPR=%.5f '
              '在线估计=%.5f 误差=%.1f%% %s | warn=%s | 重放 %s | CLI %s | '
              '复跑 %s | digest=%s'
              % (cfg['buckets'], cfg['k'], cfg['seed'],
                 a['fn_events'], '✓' if fn_ok else '✗',
                 a['measured_fpr'], a['mean_online_est_fpr'],
                 a['fpr_rel_err'] * 100, '✓' if fp_ok else '✗',
                 a['warn'],
                 '✓' if replay_ok else '✗', '✓' if cli_ok else '✗',
                 '✓' if det_ok else '✗', a['digest'][:16]))
        print('    计数统计: %s' % json.dumps(a['counts'], sort_keys=True))
    print('\n验收结论:', '全部通过 ✓' if all_ok else '存在失败项 ✗')
    return all_ok


def main():
    ap = argparse.ArgumentParser(description='计数布隆过滤器验收 harness')
    ap.add_argument('mode', choices=['test', 'run', 'accept'])
    ap.add_argument('--buckets', type=int, default=1_000_000)
    ap.add_argument('--k', type=int, default=7)
    ap.add_argument('--seed', type=int, default=11)
    ap.add_argument('--ops', type=int, default=100_000)
    ap.add_argument('--universe', type=int, default=150_000)
    ap.add_argument('--log', type=str, default=None)
    args = ap.parse_args()

    if args.mode == 'test':
        unit_tests()
    elif args.mode == 'run':
        log = args.log or 'ops_log_B%d_seed%d.jsonl' % (args.buckets,
                                                        args.seed)
        r = execute(args.buckets, args.k, args.seed, num_ops=args.ops,
                    universe=args.universe, log_path=log)
        summary = {kk: vv for kk, vv in r.items() if kk != 'req'}
        print(json.dumps(summary, indent=2, ensure_ascii=False))
        print('逐操作判定日志已写入:', log)
        ok = r['fn_events'] == 0 and replay_digest(r['req']) == r['digest']
        sys.exit(0 if ok else 1)
    else:
        sys.exit(0 if acceptance() else 1)


if __name__ == '__main__':
    main()

