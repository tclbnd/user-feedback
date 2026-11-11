"""Blend-test scene: walk -> turn cross-fade with a sweeping camera.

Shared by render_blend.py (frame output), validate.py (verification)
and regression.py (determinism checks).  Everything here is a pure
function of the frame index / time, so any frame can be rendered
directly, in order, or out of order with identical results.

Timeline (16 frames, dt = 0.16 s):
  * t in [0.0, 0.7]   pure walk cycle
  * t in [0.7, 1.6]   cross-fade walk -> turn (per-joint local blend)
  * t in [1.6, 2.4]   turn clip (root yaws 180 deg, steps toward +Z)

The camera sweeps laterally across the scene (moving roughly parallel
to its own near plane), passes right next to the character's head at
frame 8 (near-plane clipping kicks in), and pulls back out.  Because
the camera distance to the character ranges from ~9 to ~0.3, ground
fragments continuously cross cascade split boundaries.
"""

import os

from obj_loader import load_obj
from pipeline import Renderer, RenderMesh
from skinning import load_rig


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(BASE_DIR, 'assets')

WIDTH = HEIGHT = 256
FRAME_COUNT = 16
DT = 0.16

FADE_START = 0.7
FADE_LEN = 0.9

# Camera keyframes: lateral sweep with a close pass by the head; around
# frame 9 the turning character's head/arms cross the near plane.
CAM_KEYS = [
    (5.5, 3.4, 7.0),
    (2.4, 2.5, 3.4),
    (0.55, 2.1, 0.95),
    (0.25, 1.9, 0.62),
    (-2.6, 2.6, 3.8),
    (-5.5, 3.4, 7.0),
]


def frame_time(index):
    return index * DT


def _smooth(x):
    x = max(0.0, min(1.0, x))
    return x * x * (3.0 - 2.0 * x)


def blend_spec(t):
    """Action blend at absolute time t: [(action, local_time, weight)]."""
    fade = _smooth((t - FADE_START) / FADE_LEN)
    turn_t = max(0.0, t - FADE_START)
    return [('walk', t, 1.0 - fade), ('turn', turn_t, fade)]


def camera_at(index):
    """Camera eye/target for a frame index (float allowed)."""
    s = max(0.0, min(1.0, index / (FRAME_COUNT - 1))) * (len(CAM_KEYS) - 1)
    k = min(int(s), len(CAM_KEYS) - 2)
    f = _smooth(s - k)
    a, b = CAM_KEYS[k], CAM_KEYS[k + 1]
    eye = tuple(a[i] + f * (b[i] - a[i]) for i in range(3))
    # Track the character as it steps toward +Z during the turn.
    t = frame_time(index) if isinstance(index, (int, float)) else 0.0
    fade = _smooth((t - FADE_START) / FADE_LEN)
    target = (0.0, 1.3, 0.5 * fade)
    return eye, target


def load_scene():
    char_obj = load_obj(os.path.join(ASSET_DIR, 'character.obj'))
    ground_obj = load_obj(os.path.join(ASSET_DIR, 'ground.obj'))
    rig = load_rig(os.path.join(ASSET_DIR, 'character_rig.json'))
    return char_obj, ground_obj, rig


def _to_render_mesh(obj_mesh, vertices=None):
    return RenderMesh(
        vertices=vertices if vertices is not None else obj_mesh.vertices,
        uvs=obj_mesh.uvs,
        faces=obj_mesh.faces,
        texture=obj_mesh.material.texture if obj_mesh.material else None,
        kd=obj_mesh.material.kd if obj_mesh.material else (1.0, 1.0, 1.0),
    )


def build_meshes(rig, char_obj, ground_obj, t):
    """Deformed character + static ground at absolute time t."""
    skinned = rig.skin_vertices_blended(char_obj.vertices, blend_spec(t))
    return [_to_render_mesh(char_obj, skinned), _to_render_mesh(ground_obj)]


def make_renderer():
    renderer = Renderer(WIDTH, HEIGHT, shadow_size=256, num_cascades=3)
    renderer.near = 0.25
    renderer.set_camera(*camera_at(0))
    return renderer


def render_frame_at(renderer, rig, char_obj, ground_obj, index):
    """Render one frame; returns (rgb_bytes, stats)."""
    eye, target = camera_at(index)
    renderer.set_camera(eye, target)
    meshes = build_meshes(rig, char_obj, ground_obj, frame_time(index))
    renderer.render_shadow(meshes)
    return renderer.render_frame(meshes)
