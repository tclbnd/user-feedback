"""Fixed time-series driver for the format-2 character scene.

Renders a deterministic sequence chosen to cover every required event:

* walk -> turn cross-fade (1.2 s .. 2.4 s) sampled inside and at the ends;
* camera lateral near-plane-parallel slide with sub-texel steps;
* the forward near-plane grazing (2.6 s .. 3.4 s);
* fragments crossing the 10-unit cascade split (blend band exercised).

For every time it writes three 8-bit images:

* color_XX.png -- shaded color pass;
* depth_XX.png -- linear camera depth, grayscale;
* mask_XX.png  -- shadow factor (0 shadowed .. 255 lit), grayscale;

and prints a machine-readable JSON summary with the verification metrics.
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from png_texture import write_png  # noqa: E402
from scene_character import (  # noqa: E402
    CharacterScene, SCENE_DURATION, blend_weight, make_renderer)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, 'frames_character')

# Fixed timeline: event coverage is annotated per entry.
TIME_SERIES = [
    0.10,   # pure walk
    0.80,   # pure walk, lateral slide
    1.20,   # cross-fade begins (turn weight 0)
    1.55,   # mid cross-fade
    1.80,   # cross-fade, mixed quaternion poses
    2.40,   # cross-fade ends (turn weight 1)
    2.70,   # pure turn + start of near-plane grazing
    3.00,   # leading geometry crosses the near clip plane (clipped)
    3.10,   # grazing exit side, body still large in frame
    3.70,   # pure turn, camera sliding back
]


def write_gray(path, width, height, rgb_body):
    write_png(path, width, height, rgb_body)


def main(width=256, height=256, shadow_size=256):
    os.makedirs(OUT_DIR, exist_ok=True)
    scene = CharacterScene()
    renderer = make_renderer(width, height, shadow_size=shadow_size)

    report = {'times': [], 'duration': SCENE_DURATION,
              'cascade_splits': renderer.cascade_splits}
    t_start = time.perf_counter()
    for index, t in enumerate(TIME_SERIES):
        image, stats = scene.render_at(t, renderer)
        depth = renderer.depth_image()
        mask = renderer.mask_image()
        tag = '%02d' % index
        write_png(os.path.join(OUT_DIR, 'color_%s.png' % tag),
                  width, height, image)
        write_gray(os.path.join(OUT_DIR, 'depth_%s.png' % tag),
                   width, height, depth)
        write_gray(os.path.join(OUT_DIR, 'mask_%s.png' % tag),
                   width, height, mask)
        eye, target = scene.camera_at(t)
        report['times'].append({
            'index': index, 'time': t,
            'turn_weight': round(blend_weight(t), 4),
            'camera_eye': [round(c, 4) for c in eye],
            'tris_drawn': stats['tris_drawn'],
            'fragments': stats['fragments'],
            'shadowed_pct': round(stats['shadowed_pct'], 3),
            'cascade_hits': stats['cascade_hits'],
            'blend_band_fragments': stats['blend_hits'],
        })
        print('t=%.2f turn=%.2f tris=%3d frag=%6d shadowed=%5.1f%% '
              'cascades=%s blend=%d -> color/depth/mask_%s.png'
              % (t, blend_weight(t), stats['tris_drawn'],
                 stats['fragments'], stats['shadowed_pct'],
                 stats['cascade_hits'], stats['blend_hits'], tag))
    report['wall_seconds'] = round(time.perf_counter() - t_start, 3)

    with open(os.path.join(OUT_DIR, 'series_report.json'), 'w',
              encoding='utf-8') as fh:
        json.dump(report, fh, indent=2)
    print('wrote %d frame triples to %s (%.2fs)'
          % (len(TIME_SERIES), os.path.relpath(OUT_DIR, BASE_DIR),
             report['wall_seconds']))


if __name__ == '__main__':
    main()
