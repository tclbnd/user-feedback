"""Determinism and frame-isolation verification.

* The same time rendered directly (random access) and inside a sequential
  run must produce bit-identical color, depth and shadow-mask buffers.
* Rendering a different time in between must not leak any pose or buffer
  state into the next frame (the skeleton/evaluator hold no pose cache).
* The legacy bar scene must remain byte-identical to the captured golden
  PNG hashes, proving the original pipeline path was not perturbed.
"""

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from obj_loader import load_obj  # noqa: E402
from pipeline import Renderer, RenderMesh  # noqa: E402
from png_texture import write_png  # noqa: E402
from scene_character import CharacterScene, make_renderer  # noqa: E402
from skinning import load_rig  # noqa: E402

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSET_DIR = os.path.join(BASE_DIR, 'assets')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def render_buffers(scene, renderer, time):
    """Render one time and snapshot all three buffers as hashes."""
    image, _ = scene.render_at(time, renderer)
    return {
        'color': _digest(image),
        'depth': _digest(renderer.depth_image()),
        'mask': _digest(renderer.mask_image()),
    }


def test_random_vs_sequential():
    scene = CharacterScene()
    probe_times = [0.31, 1.73, 2.62, 3.37]
    # A fixed interleaved order exercising blend, pure turn and grazing.
    sequence = [0.05, 2.62, 0.95, 1.73, 3.37, 0.31, 2.1, 1.2, 3.8]

    # Direct random access: fresh renderer, each probe rendered alone.
    direct = {}
    for t in probe_times:
        r = make_renderer(128, 128, shadow_size=128)
        direct[t] = render_buffers(scene, r, t)

    # Sequential access: one renderer walked through the whole sequence.
    seq_renderer = make_renderer(128, 128, shadow_size=128)
    sequential = {}
    for t in sequence:
        result = render_buffers(scene, seq_renderer, t)
        if t in probe_times:
            sequential[t] = result

    mismatches = []
    for t in probe_times:
        for buf in ('color', 'depth', 'mask'):
            if direct[t][buf] != sequential[t][buf]:
                mismatches.append((t, buf))
    return {'probe_times': probe_times,
            'sequence': sequence,
            'mismatches': mismatches,
            'passed': not mismatches}


def test_pose_cache_isolation():
    """No evaluator state may carry between two evaluations."""
    scene = CharacterScene()
    rig = scene.rig
    mesh = scene.char_obj
    # Evaluate a "polluting" pose, then a probe pose, then the probe again
    # on a brand-new evaluator path.  Geometry must match exactly.
    _ = rig.evaluate_blend([('walk', 2.9, 0.3), ('turn', 1.1, 0.7)])
    probe_a = rig.skin_vertices_pose(
        mesh.vertices, rig.evaluate_clip('turn', 0.55))
    _ = rig.evaluate_clip('walk', 0.01)
    probe_b = rig.skin_vertices_pose(
        mesh.vertices, rig.evaluate_clip('turn', 0.55))
    same_geometry = all(
        abs(a[k] - b[k]) < 1e-15
        for a, b in zip(probe_a, probe_b) for k in range(3))

    # Renderer frame buffers must be fully overwritten each frame too:
    # a render at t1 then t0 equals a renderer that only ever saw t0.
    r1 = make_renderer(96, 96, shadow_size=96)
    scene.render_at(2.9, r1)
    buf_polluted = render_buffers(scene, r1, 0.55)
    r2 = make_renderer(96, 96, shadow_size=96)
    buf_clean = render_buffers(scene, r2, 0.55)
    buffers_clean = all(
        buf_polluted[k] == buf_clean[k]
        for k in ('color', 'depth', 'mask'))
    return {'geometry_isolated': same_geometry,
            'buffers_isolated': buffers_clean,
            'passed': same_geometry and buffers_clean}


def test_unordered_order_invariance():
    """Shuffling render order cannot change any individual frame."""
    scene = CharacterScene()
    times = [0.4, 1.5, 2.3, 3.1]
    order_a = list(times)
    order_b = list(reversed(times))
    order_c = [times[2], times[0], times[3], times[1]]

    def collect(order):
        r = make_renderer(96, 96, shadow_size=96)
        out = {}
        for t in order:
            out[t] = render_buffers(scene, r, t)
        return out

    runs = [collect(order_a), collect(order_b), collect(order_c)]
    mismatches = []
    for t in times:
        ref = runs[0][t]
        for other in runs[1:]:
            for buf in ('color', 'depth', 'mask'):
                if ref[buf] != other[t][buf]:
                    mismatches.append((t, buf))
    return {'orders': [order_a, order_b, order_c],
            'mismatches': mismatches,
            'passed': not mismatches}


def test_bar_golden_regression():
    """The legacy bar scene renders byte-identical golden frames."""
    golden_path = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               'golden_bar_sha256.json')
    with open(golden_path, encoding='utf-8') as fh:
        golden = json.load(fh)

    bar_obj = load_obj(os.path.join(ASSET_DIR, 'bar.obj'))
    ground_obj = load_obj(os.path.join(ASSET_DIR, 'ground.obj'))
    rig = load_rig(os.path.join(ASSET_DIR, 'rig.json'))
    ground = RenderMesh(
        ground_obj.vertices, ground_obj.uvs, ground_obj.faces,
        ground_obj.material.texture, ground_obj.material.kd)
    renderer = Renderer(256, 256, shadow_size=256)

    out_dir = os.path.join(os.environ.get('TMPDIR', '/tmp'),
                           'bar_regression')
    os.makedirs(out_dir, exist_ok=True)
    failures = []
    for frame_index in range(rig.frame_count):
        skinned = rig.skin_vertices(bar_obj.vertices, frame_index)
        bar = RenderMesh(
            skinned, bar_obj.uvs, bar_obj.faces,
            bar_obj.material.texture, bar_obj.material.kd)
        renderer.render_shadow([bar, ground])
        image, _ = renderer.render_frame([bar, ground])
        name = 'frame_%02d.png' % frame_index
        out_path = os.path.join(out_dir, name)
        write_png(out_path, 256, 256, image)
        # Golden hashes were captured from the encoded PNG file bytes, so
        # hash the file, not the in-memory RGB body.
        with open(out_path, 'rb') as fh:
            file_digest = _digest(fh.read())
        if file_digest != golden[name]:
            failures.append(name)
    return {'frames': rig.frame_count,
            'failures': failures,
            'passed': not failures}


def run():
    results = {}
    results['random_vs_sequential'] = test_random_vs_sequential()
    results['pose_cache_isolation'] = test_pose_cache_isolation()
    results['unordered_invariance'] = test_unordered_order_invariance()
    results['bar_golden_regression'] = test_bar_golden_regression()
    results['passed'] = all(v['passed'] for v in results.values())
    return results


if __name__ == '__main__':
    print(json.dumps(run(), indent=2, default=str))
