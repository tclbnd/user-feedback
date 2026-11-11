"""Quick manual smoke test for the format-2 character scene."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scene_character import CharacterScene, make_renderer  # noqa: E402
from png_texture import write_png  # noqa: E402


def main():
    scene = CharacterScene()
    renderer = make_renderer(128, 128, shadow_size=128)

    p0 = scene.pose_at(0.3)
    p1 = scene.pose_at(0.3)
    v0 = scene.rig.skin_vertices_pose(scene.char_obj.vertices, p0)
    v1 = scene.rig.skin_vertices_pose(scene.char_obj.vertices, p1)
    assert all(abs(a[k] - b[k]) < 1e-12
               for a, b in zip(v0, v1) for k in range(3)), 'not deterministic'
    print('joints:', scene.rig.order)
    print('clips:', list(scene.rig.clips))
    print('root/head world pos at t=1.8 (blend):',
          scene.pose_at(1.8).joint_world_position('root'),
          scene.pose_at(1.8).joint_world_position('head'))

    out_dir = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), 'smoke_out')
    os.makedirs(out_dir, exist_ok=True)
    for t in (0.0, 1.5, 2.0, 3.0):
        image, stats = scene.render_at(t, renderer)
        write_png(os.path.join(out_dir, 'smoke_%.1f.png' % t),
                  128, 128, image)
        print('t=%.1f' % t, stats)
    print('OK ->', out_dir)


if __name__ == '__main__':
    main()
