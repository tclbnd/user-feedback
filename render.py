"""Render the full skinned-bar animation to frames/frame_XX.png.

Run:  python3 render.py
"""

import os
import time

from obj_loader import load_obj
from pipeline import Renderer, RenderMesh
from png_texture import write_png
from skinning import load_rig


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(BASE_DIR, 'assets')
FRAME_DIR = os.path.join(BASE_DIR, 'frames')

WIDTH = HEIGHT = 256


def build_static_mesh(obj_mesh):
    return RenderMesh(
        vertices=obj_mesh.vertices,
        uvs=obj_mesh.uvs,
        faces=obj_mesh.faces,
        texture=obj_mesh.material.texture if obj_mesh.material else None,
        kd=obj_mesh.material.kd if obj_mesh.material else (1.0, 1.0, 1.0),
    )


def main():
    os.makedirs(FRAME_DIR, exist_ok=True)

    bar_obj = load_obj(os.path.join(ASSET_DIR, 'bar.obj'))
    ground_obj = load_obj(os.path.join(ASSET_DIR, 'ground.obj'))
    rig = load_rig(os.path.join(ASSET_DIR, 'rig.json'))

    ground = build_static_mesh(ground_obj)
    bar_material = bar_obj.material

    renderer = Renderer(WIDTH, HEIGHT, shadow_size=256)

    print('rendering %d frames at %dx%d ...'
          % (rig.frame_count, WIDTH, HEIGHT))
    total_start = time.perf_counter()
    for frame_index in range(rig.frame_count):
        t0 = time.perf_counter()

        # 1. skin the bar for this animation frame
        skinned = rig.skin_vertices(bar_obj.vertices, frame_index)
        bar = RenderMesh(
            vertices=skinned,
            uvs=bar_obj.uvs,
            faces=bar_obj.faces,
            texture=bar_material.texture if bar_material else None,
            kd=bar_material.kd if bar_material else (1.0, 1.0, 1.0),
        )

        # 2. shadow depth pass from the light's point of view
        renderer.render_shadow([bar, ground])

        # 3. main shaded pass with shadow comparison + Phong
        image, stats = renderer.render_frame([bar, ground])

        out_path = os.path.join(
            FRAME_DIR, 'frame_%02d.png' % frame_index)
        write_png(out_path, WIDTH, HEIGHT, image)
        elapsed = time.perf_counter() - t0

        print('frame %02d: tris=%3d fragments=%6d shadowed=%6.2f%% '
              'time=%6.3fs -> %s'
              % (frame_index, stats['tris_drawn'],
                 stats['fragments'], stats['shadowed_pct'],
                 elapsed, os.path.relpath(out_path, BASE_DIR)))

    total = time.perf_counter() - total_start
    print('done: %d frames in %.2fs (avg %.3fs/frame)'
          % (rig.frame_count, total, total / rig.frame_count))


if __name__ == '__main__':
    main()
