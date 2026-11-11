"""One-click local regression for the soft-3d-pipeline upgrade.

Runs, in order:

1. legacy bar-scene golden-hash regression (byte-identical frames);
2. independent skeleton/skinning math verification;
3. shadow occlusion / cascade-continuity / sub-texel stability;
4. random-access vs sequential rendering and pose-cache isolation.

Exit code is non-zero if any suite fails.  Stdlib only; no arguments.

    python3 run_regression.py
"""

import os
import sys
import time

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
sys.path.insert(0, os.path.join(BASE_DIR, 'tests'))

import test_determinism  # noqa: E402
import test_shadows  # noqa: E402
import test_skeleton_math  # noqa: E402


def _format_skeleton(result):
    spike = result['blend_max_jump']
    return (
        '  non-uniform-scale joints : %d\n'
        '  joint endpoint checks    : %d (max err %.1e)\n'
        '  selected vertex max err  : %.1e\n'
        '  quaternion hemisphere err: %.1e\n'
        '  single-layer blend exact : %s\n'
        '  cross-fade max spike     : %s'
        % (result['non_uniform_joints'],
           result['endpoint_checks'], result['endpoint_max_err'],
           result['vertex_max_err'], result['quat_hemisphere_err'],
           result['single_layer_exact'], spike))


def main():
    suites = [
        ('skeleton math (FK, LBS, quaternion equivalence, cross-fade)',
         test_skeleton_math.run),
        ('shadows (ray occlusion, CSM continuity, sub-texel stability)',
         test_shadows.run),
        ('determinism (random vs sequential, cache isolation, bar golden)',
         test_determinism.run),
    ]
    overall_ok = True
    started = time.perf_counter()
    summaries = []
    for title, runner in suites:
        print('=' * 72)
        print('SUITE:', title)
        print('-' * 72)
        t0 = time.perf_counter()
        try:
            result = runner()
        except Exception as exc:  # report, don't crash the whole run
            result = {'passed': False, 'error': repr(exc)}
            import traceback
            traceback.print_exc()
        elapsed = time.perf_counter() - t0
        ok = bool(result.get('passed'))
        overall_ok = overall_ok and ok
        summaries.append((title, ok, elapsed))
        if 'ray_occlusion' in result:
            ro = result['ray_occlusion']
            cc = result['cascade_continuity']
            st = result['subtexel_stability']
            print('  ray/triangle occlusion agreement: %d/%d = %.2f%%'
                  % (ro['agreed'], ro['total'], 100.0 * ro['rate']))
            print('  cascade split max factor delta  : %.4f '
                  '(%d blend samples, %d index switch)'
                  % (cc['max_factor_delta'], cc['blend_samples'],
                     cc['index_switches']))
            print('  static-ground inter-frame change : %d/%d pixels '
                  '(%.4f%%), %d edge crossings, mean |dmask|=%.4f'
                  % (st['max_changed_pixels'], st['flat_region_pixels'],
                     100.0 * st['change_rate'], st['edge_crossings'],
                     st['mean_abs_mask_change']))
        if 'random_vs_sequential' in result:
            rvs = result['random_vs_sequential']
            iso = result['pose_cache_isolation']
            unord = result['unordered_invariance']
            bar = result['bar_golden_regression']
            print('  random vs sequential buffer mismatches: %s'
                  % rvs['mismatches'])
            print('  unordered render mismatches            : %s'
                  % unord['mismatches'])
            print('  pose/buffer cache isolated             : geo=%s buf=%s'
                  % (iso['geometry_isolated'], iso['buffers_isolated']))
            print('  legacy bar golden frames                : %d/%d match'
                  % (bar['frames'] - len(bar['failures']), bar['frames']))
        if 'endpoint_checks' in result:
            print(_format_skeleton(result))
        print('  RESULT: %s (%.2fs)' % ('PASS' if ok else 'FAIL', elapsed))

    print('=' * 72)
    print('REGRESSION SUMMARY')
    print('-' * 72)
    for title, ok, elapsed in summaries:
        print('  [%s] %-58s %5.2fs'
              % ('PASS' if ok else 'FAIL', title, elapsed))
    total = time.perf_counter() - started
    print('-' * 72)
    print('TOTAL: %.2fs -- %s' % (total, 'ALL PASS' if overall_ok
                                   else 'FAILURES PRESENT'))
    return 0 if overall_ok else 1


if __name__ == '__main__':
    sys.exit(main())
