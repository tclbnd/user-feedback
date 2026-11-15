import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import pitch
import validate as V

clean = V.make_clean()
sig = V.add_noise(clean, 20)
for fs in V.FRAME_SIZES:
    t0 = time.perf_counter()
    res = pitch.analyze(sig, V.RATE, fs, V.HOP)
    dt = time.perf_counter() - t0
    nfr = len(res["frames"])
    print(f"fs={fs} frames={nfr} 时长={V.DURATION}s 用时={dt:.2f}s "
          f"({1000 * dt / nfr:.1f} ms/帧, 快于实时 {V.DURATION / dt:.1f}x)")
