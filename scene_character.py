"""Format-2 character scene: walk -> turn cross-fade with a moving camera.

The scene is a pure function of a time value:

* ``pose_at(time)`` evaluates a walk/turn cross-fade on the 12-joint
  skeleton and skins one shared vertex buffer;
* ``camera_at(time)`` moves the camera laterally (parallel to its near
  plane, including sub-texel steps) and through a near-plane grazing;
* ``render_at(time, renderer)`` feeds the *same* deformed geometry to
  both the shadow depth passes and the color pass.

No pose or geometry is cached between calls, so random access and
sequential rendering of the same time are bit-identical.
"""

import math
import os

import math3d
from obj_loader import load_obj
from pipeline import RenderMesh
from skinning import load_rig
from csm_pipeline import CascadeRenderer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ASSET_DIR = os.path.join(BASE_DIR, 'assets')

# Timeline (seconds).
WALK_DURATION = 2.0
TURN_DURATION = 1.6
FADE_START = 1.2          # cross-fade begins
FADE_END = 2.4            # cross-fade ends (pure turn afterwards)
SCENE_DURATION = 4.0

# Camera path: lateral slide parallel to the near plane, then a forward
# grazing pass toward / past the near plane, then slide back.
CAM_BASE = (4.2, 3.0, 6.2)
CAM_TARGET_BASE = (0.0, 1.4, 0.4)


def blend_weight(time):
    """Turn-clip weight over the timeline (walk weight = 1 - turn)."""
    if time <= FADE_START:
        return 0.0
    if time >= FADE_END:
        return 1.0
    # smoothstep for a ease-in/ease-out cross-fade
    t = (time - FADE_START) / (FADE_END - FADE_START)
    return t * t * (3.0 - 2.0 * t)


class CharacterScene:
    """Loads the format-2 rig/mesh once; all frame state stays local."""

    def __init__(self, asset_dir=ASSET_DIR):
        self.char_obj = load_obj(os.path.join(asset_dir, 'character.obj'))
        self.ground_obj = load_obj(os.path.join(asset_dir, 'ground.obj'))
        self.rig = load_rig(os.path.join(asset_dir, 'character_rig.json'))

    def pose_at(self, time):
        """Evaluate the blended pose at an arbitrary time (seconds)."""
        time = time % SCENE_DURATION
        w_turn = blend_weight(time)
        if w_turn <= 0.0:
            return self.rig.evaluate_clip('walk', time)
        if w_turn >= 1.0:
            return self.rig.evaluate_clip(
                'turn', time - FADE_END if time > FADE_END else
                (time - FADE_START))
        # walk keeps looping on its own clock; the turn clip starts at 0
        # exactly when the fade begins.
        turn_time = max(0.0, time - FADE_START)
        return self.rig.evaluate_blend([
            ('walk', time, 1.0 - w_turn),
            ('turn', turn_time, w_turn)])

    def skinned_mesh_at(self, time):
        """One deformed geometry used by BOTH color and shadow passes."""
        pose = self.pose_at(time)
        verts = self.rig.skin_vertices_pose(self.char_obj.vertices, pose)
        mat = self.char_obj.material
        return RenderMesh(
            vertices=verts,
            uvs=self.char_obj.uvs,
            faces=self.char_obj.faces,
            texture=mat.texture if mat else None,
            kd=mat.kd if mat else (1.0, 1.0, 1.0)), pose

    def ground_mesh(self):
        mat = self.ground_obj.material
        return RenderMesh(
            vertices=self.ground_obj.vertices,
            uvs=self.ground_obj.uvs,
            faces=self.ground_obj.faces,
            texture=mat.texture if mat else None,
            kd=mat.kd if mat else (1.0, 1.0, 1.0))

    def camera_at(self, time):
        """Lateral near-plane-parallel motion plus a forward grazing."""
        time = time % SCENE_DURATION
        # 1) lateral slide: total amplitude crosses many shadow texels,
        #    including deliberately tiny sub-texel increments;
        # 2) a forward dolly between 2.6s and 3.4s moves the camera along
        #    its own view axis until the character's foremost geometry
        #    crosses the 0.1 near plane, keeping the torso centered.
        lateral = 2.6 * math.sin(2.0 * math.pi * time / SCENE_DURATION)
        # sub-texel wobble (~2 mm, shadow texels are cm..dm sized)
        lateral += 0.002 * math.sin(2.0 * math.pi * time * 7.0)

        # Resting (non-grazing) camera frame.
        base_eye = CAM_BASE
        base_target = CAM_TARGET_BASE
        forward_axis = math3d.normalize(
            math3d.sub(base_target, base_eye))

        dolly = 0.0
        if 2.6 <= time <= 3.4:
            u = (time - 2.6) / 0.8
            # Peak ~7.1 units along the view axis lets the foremost
            # vertices cross the 0.1 near plane (clipped) while the torso
            # stays large and readable: an extreme grazing, not pass-through.
            dolly = 7.05 * math.sin(math.pi * u)
            # Freeze gross lateral motion during the dolly; the sub-texel
            # wobble still exercises shadow-grid stability.
            lateral = 0.002 * math.sin(2.0 * math.pi * time * 7.0)

        eye = (base_eye[0] + lateral
               + forward_axis[0] * dolly,
               base_eye[1]
               + forward_axis[1] * dolly
               + 0.15 * math.sin(2.0 * math.pi * time / 4.0),
               base_eye[2] + lateral * 0.0
               + forward_axis[2] * dolly)
        target = (base_target[0] + 0.6 * lateral,
                  base_target[1],
                  base_target[2])
        return eye, target

    def render_at(self, time, renderer):
        """Skin once, then shadow + color from the shared geometry."""
        char, _pose = self.skinned_mesh_at(time)
        ground = self.ground_mesh()
        eye, target = self.camera_at(time)
        renderer.set_camera(eye, target)
        meshes = [char, ground]
        renderer.render_shadow(meshes)
        image, stats = renderer.render_frame(meshes)
        return image, stats


def make_renderer(width=256, height=256, shadow_size=256):
    # Three cascades; the middle split (10 world units) is crossed by the
    # character during the timeline so blend-band continuity is exercised.
    return CascadeRenderer(
        width, height, shadow_size=shadow_size,
        cascade_splits=(0.1, 4.5, 10.0, 32.0),
        blend_fraction=0.15)
