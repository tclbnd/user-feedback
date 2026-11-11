"""One-click regression for the soft-3d-pipeline (stdlib only).

Covers the legacy bar scene and the walk->turn blend scene:

1. Sequential vs shuffled vs direct random-access rendering must be
   byte-identical for every frame (poses, skinning, cascades and all
   buffers are pure functions of the frame index).
2. The pose cache must never pollute a later frame: re-rendering a
   frame after many other frames went through the cache must reproduce
   the original bytes, and cache-on must equal cache-off.

Run:  python3 regression.py   (exit code 0 = all green)
"""

import hashlib
import os
import sys
import time

import blend_scene
from obj_loader import load_obj
from pipeline import Renderer, RenderMesh
from skinning import load_rig


ASSET_DIR = blend_scene.ASSET_DIR
BAR_FRAMES = 12
BLEND_FRAMES = [0, 3, 5, 7, 8, 9, 12, 15]   # walk, fade, clip, turned


def digest(data):
    return hashlib.sha256(data).hexdigest()[:12]


# ----------------------------------------------------------------------
# Bar scene (same construction as render.py)
# ----------------------------------------------------------------------
def load_bar_scene():
    bar_obj = load_obj(os.path.join(ASSET_DIR, 'bar.obj'))
    ground_obj = load_obj(os.path.join(ASSET_DIR, 'ground.obj'))
    rig = load_rig(os.path.join(ASSET_DIR, 'rig.json'))
    ground = RenderMesh(
        vertices=ground_obj.vertices, uvs=ground_obj.uvs,
        faces=ground_obj.faces,
        texture=ground_obj.material.texture,
        kd=ground_obj.material.kd)
    return bar_obj, ground, rig


def render_bar_frame(renderer, rig, bar_obj, ground, frame_index):
    skinned = rig.skin_vertices(bar_obj.vertices, frame_index)
    bar = RenderMesh(
        vertices=skinned, uvs=bar_obj.uvs, faces=bar_obj.faces,
        texture=bar_obj.material.texture, kd=bar_obj.material.kd)
    renderer.render_shadow([bar, ground])
    image, _ = renderer.render_frame([bar, ground])
    return image


def check_bar_scene():
    print('--- bar scene: sequential vs shuffled ---')
    bar_obj, ground, rig = load_bar_scene()
    renderer = Renderer(blend_scene.WIDTH, blend_scene.HEIGHT,
                        shadow_size=256)

    sequential = {}
    for i in range(BAR_FRAMES):
        sequential[i] = render_bar_frame(renderer, rig, bar_obj, ground, i)

    order = [5, 11, 2, 8, 0, 9, 3, 7, 1, 10, 6, 4]
    mismatches = 0
    for i in order:
        image = render_bar_frame(renderer, rig, bar_obj, ground, i)
        if image != sequential[i]:
            mismatches += 1
            print('  frame %02d MISMATCH seq=%s shuffled=%s'
                  % (i, digest(sequential[i]), digest(image)))
    print('  %d frames, %d mismatches (first hashes: %s)'
          % (BAR_FRAMES, mismatches,
             ' '.join(digest(sequential[i]) for i in range(3))))
    ok = mismatches == 0
    print('bar scene: %s' % ('PASS' if ok else 'FAIL'))
    return ok


# ----------------------------------------------------------------------
# Blend scene: sequential / shuffled / direct random access
# ----------------------------------------------------------------------
def check_blend_scene():
    print('--- blend scene: sequential vs shuffled vs direct ---')
    char_obj, ground_obj, rig = blend_scene.load_scene()

    sequential = {}
    renderer = blend_scene.make_renderer()
    for i in BLEND_FRAMES:
        image, _ = blend_scene.render_frame_at(
            renderer, rig, char_obj, ground_obj, i)
        sequential[i] = image

    mismatches = 0
    order = [9, 0, 12, 5, 15, 3, 8, 7]
    for i in order:
        image, _ = blend_scene.render_frame_at(
            renderer, rig, char_obj, ground_obj, i)
        if image != sequential[i]:
            mismatches += 1
            print('  frame %02d shuffled MISMATCH' % i)

    # Direct random access: fresh renderer + fresh rig per frame.
    for i in [9, 5, 12, 0]:
        fresh_rig = load_rig(os.path.join(ASSET_DIR, 'character_rig.json'))
        fresh_renderer = blend_scene.make_renderer()
        image, _ = blend_scene.render_frame_at(
            fresh_renderer, fresh_rig, char_obj, ground_obj, i)
        if image != sequential[i]:
            mismatches += 1
            print('  frame %02d direct MISMATCH seq=%s direct=%s'
                  % (i, digest(sequential[i]), digest(image)))
    print('  %d frames x (sequential, shuffled, direct), %d mismatches'
          % (len(BLEND_FRAMES), mismatches))
    ok = mismatches == 0
    print('blend scene: %s' % ('PASS' if ok else 'FAIL'))
    return ok


# ----------------------------------------------------------------------
# Pose-cache pollution
# ----------------------------------------------------------------------
def check_cache_pollution():
    print('--- pose cache: pollution and cache-on/off equivalence ---')
    char_obj, ground_obj, rig = blend_scene.load_scene()
    renderer = blend_scene.make_renderer()

    # Render frame 7, churn the cache with every other frame, render 7
    # again: bytes must be identical.
    first, _ = blend_scene.render_frame_at(
        renderer, rig, char_obj, ground_obj, 7)
    for i in list(range(0, 7)) + list(range(8, blend_scene.FRAME_COUNT)):
        blend_scene.render_frame_at(renderer, rig, char_obj, ground_obj, i)
    again, _ = blend_scene.render_frame_at(
        renderer, rig, char_obj, ground_obj, 7)
    pollution_ok = first == again
    print('  frame 07 before/after cache churn: %s (%s vs %s)'
          % ('identical' if pollution_ok else 'POLLUTED',
             digest(first), digest(again)))

    # Cache disabled must produce the same images as cache enabled.
    rig_off = load_rig(os.path.join(ASSET_DIR, 'character_rig.json'))
    rig_off.cache_enabled = False
    renderer_off = blend_scene.make_renderer()
    cache_mismatch = 0
    for i in BLEND_FRAMES:
        on, _ = blend_scene.render_frame_at(
            renderer, rig, char_obj, ground_obj, i)
        off, _ = blend_scene.render_frame_at(
            renderer_off, rig_off, char_obj, ground_obj, i)
        if on != off:
            cache_mismatch += 1
            print('  frame %02d cache on/off MISMATCH' % i)
    print('  cache on/off: %d/%d frames differ'
          % (cache_mismatch, len(BLEND_FRAMES)))
    ok = pollution_ok and cache_mismatch == 0
    print('pose cache: %s' % ('PASS' if ok else 'FAIL'))
    return ok


def main():
    start = time.perf_counter()
    results = [
        check_bar_scene(),
        check_blend_scene(),
        check_cache_pollution(),
    ]
    elapsed = time.perf_counter() - start
    print('== regression %s (%.1fs) =='
          % ('PASS' if all(results) else 'FAIL', elapsed))
    sys.exit(0 if all(results) else 1)


if __name__ == '__main__':
    main()
