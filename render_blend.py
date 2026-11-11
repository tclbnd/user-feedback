"""Render the walk->turn blend scene: color + depth + shadow mask.

Run:  python3 render_blend.py

Writes frames_blend/blend_XX.png (color), blend_XX_depth.png (view
depth) and blend_XX_shadow.png (shadow mask) for a fixed 16-frame
sequence covering the action switch, the close camera pass (near-plane
crossing) and cascade-boundary crossings.
"""

import os
import time

import blend_scene
from png_texture import write_png


FRAME_DIR = os.path.join(blend_scene.BASE_DIR, 'frames_blend')


def main():
    os.makedirs(FRAME_DIR, exist_ok=True)
    char_obj, ground_obj, rig = blend_scene.load_scene()
    renderer = blend_scene.make_renderer()

    print('rendering %d blend frames at %dx%d (cascades=%d) ...'
          % (blend_scene.FRAME_COUNT, blend_scene.WIDTH,
             blend_scene.HEIGHT, renderer.num_cascades))
    total_start = time.perf_counter()
    for index in range(blend_scene.FRAME_COUNT):
        t0 = time.perf_counter()
        image, stats = blend_scene.render_frame_at(
            renderer, rig, char_obj, ground_obj, index)

        stem = os.path.join(FRAME_DIR, 'blend_%02d' % index)
        write_png(stem + '.png', blend_scene.WIDTH, blend_scene.HEIGHT,
                  image)
        write_png(stem + '_depth.png', blend_scene.WIDTH,
                  blend_scene.HEIGHT, renderer.depth_image())
        write_png(stem + '_shadow.png', blend_scene.WIDTH,
                  blend_scene.HEIGHT, renderer.shadow_mask_image())
        elapsed = time.perf_counter() - t0

        t = blend_scene.frame_time(index)
        spec = blend_scene.blend_spec(t)
        print('frame %02d: t=%4.2f walk=%.2f turn=%.2f tris=%3d '
              'clipped=%2d fragments=%6d shadowed=%6.2f%% time=%5.2fs'
              % (index, t, spec[0][2], spec[1][2], stats['tris_drawn'],
                 stats['tris_clipped'], stats['fragments'],
                 stats['shadowed_pct'], elapsed))

    total = time.perf_counter() - total_start
    print('done: %d frames in %.2fs (avg %.3fs/frame)'
          % (blend_scene.FRAME_COUNT, total,
             total / blend_scene.FRAME_COUNT))


if __name__ == '__main__':
    main()
