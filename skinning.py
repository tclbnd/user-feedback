"""Linear-blend skinning driven by rig.json (stdlib only).

Two rig flavours are supported:

* **legacy** — a single ``animation`` record rotating one joint around an
  axis for a fixed number of frames (the original bar rig).  The old
  entry points (``frame_count`` / ``pose_world(frame)`` /
  ``skin_vertices(vertices, frame)``) behave exactly as before.
* **actions** — an ``actions`` map of named clips with per-joint TRS
  keyframe tracks (translation / rotation-quaternion / scale).  Joints
  may carry bind-pose ``rotation`` and (possibly non-uniform) ``scale``,
  and the hierarchy may be arbitrarily deep.  Clips are sampled at any
  continuous time and several clips can be cross-faded by blending each
  joint's *local* pose (lerp translations/scales, hemisphere-aligned
  quaternion blending) before composing world transforms.

Pose evaluation is a pure function of its inputs: rendering any time
directly, in sequence, or out of order yields identical results.  An
optional memoization cache only ever stores immutable results keyed by
the exact request, so it can never pollute a later frame.

Skinning matrix per joint:  ``skin_j = pose_world_j * inverse(bind_world_j)``
with a general affine inverse so non-uniform bind scales are handled.
Deformed vertices are the weighted sum

    v' = sum_j weight(v, j) * skin_j * v
"""

import bisect
import json
import math

import math3d


class Rig:
    def __init__(self, path):
        with open(path, 'r', encoding='utf-8') as fh:
            data = json.load(fh)

        self.joints = {}
        self.order = []
        for joint in data['joints']:
            name = joint['name']
            self.joints[name] = {
                'parent': joint['parent'],
                'position': tuple(float(c) for c in joint['position']),
                'rotation': tuple(float(c) for c in joint.get(
                    'rotation', (0.0, 0.0, 0.0, 1.0))),
                'scale': tuple(float(c) for c in joint.get(
                    'scale', (1.0, 1.0, 1.0))),
            }
            self.order.append(name)

        # weights: per vertex, list of (joint_name, weight)
        self.weights = [
            [(entry[0], float(entry[1])) for entry in vertex_weights]
            for vertex_weights in data['weights']
        ]

        # Bind-pose local TRS and world transforms.
        self.bind_local = {
            name: (self.joints[name]['position'],
                   math3d.quat_normalize(self.joints[name]['rotation']),
                   self.joints[name]['scale'])
            for name in self.order
        }
        self.bind_world = {}
        for name in self.order:
            t, q, s = self.bind_local[name]
            local = math3d.trs_matrix(t, q, s)
            parent = self.joints[name]['parent']
            if parent is None:
                self.bind_world[name] = local
            else:
                self.bind_world[name] = math3d.mul(
                    self.bind_world[parent], local)

        # Legacy single-joint axis-angle animation (optional).
        anim = data.get('animation')
        self.animated_joint = None
        if anim is not None:
            self.animated_joint = anim['joint']
            self.axis = tuple(anim['axis'])
            self.frame_count = int(anim['frames'])
            self.angles = [math.radians(float(a))
                           for a in anim['angle_degrees']]
            if len(self.angles) != self.frame_count:
                raise ValueError('animation frame count / angle list mismatch')
            # Legacy rigs only ever used translation+rotation binds; keep
            # the rigid inverse so legacy output is bit-identical.
            self.bind_inverse = {
                name: invert_rigid(self.bind_world[name])
                for name in self.order
            }
        else:
            self.frame_count = 0
            self.bind_inverse = {
                name: math3d.invert_affine(self.bind_world[name])
                for name in self.order
            }

        # Named action clips with TRS keyframe tracks.
        self.actions = {}
        for action_name, action in data.get('actions', {}).items():
            tracks = {}
            for joint_name, track in action.get('tracks', {}).items():
                if joint_name not in self.joints:
                    raise ValueError('action %r references unknown joint %r'
                                     % (action_name, joint_name))
                times = [float(t) for t in track['times']]
                if len(times) < 1 or any(
                        times[i] >= times[i + 1]
                        for i in range(len(times) - 1)):
                    raise ValueError('action %r joint %r: times must be '
                                     'strictly increasing'
                                     % (action_name, joint_name))
                channels = {}
                for key, chan in (('translations', 't'),
                                  ('rotations', 'q'),
                                  ('scales', 's')):
                    if key in track:
                        values = [tuple(float(c) for c in v)
                                  for v in track[key]]
                        if len(values) != len(times):
                            raise ValueError(
                                'action %r joint %r: %s count mismatch'
                                % (action_name, joint_name, key))
                        channels[chan] = values
                tracks[joint_name] = (times, channels)
            self.actions[action_name] = {
                'duration': float(action['duration']),
                'loop': bool(action.get('loop', True)),
                'tracks': tracks,
            }

        # Memoization for pose requests; values are never mutated, so a
        # hit returns exactly what a fresh evaluation would produce.
        self.cache_enabled = True
        self._local_pose_cache = {}
        self._skin_cache = {}

    # ------------------------------------------------------------------
    # Action sampling
    # ------------------------------------------------------------------
    def _clip_time(self, action, time):
        duration = action['duration']
        if action['loop']:
            return time % duration
        return max(0.0, min(time, duration))

    @staticmethod
    def _sample_channel(times, values, t, is_quat):
        if t <= times[0]:
            return values[0]
        if t >= times[-1]:
            return values[-1]
        idx = bisect.bisect_right(times, t) - 1
        t0, t1 = times[idx], times[idx + 1]
        f = (t - t0) / (t1 - t0)
        a, b = values[idx], values[idx + 1]
        if is_quat:
            return math3d.quat_slerp(a, b, f)
        return tuple(a[i] + f * (b[i] - a[i]) for i in range(3))

    def sample_action_local(self, action_name, time):
        """Local (t, q, s) of every joint for one clip at one time."""
        action = self.actions[action_name]
        t = self._clip_time(action, time)
        pose = {}
        for name in self.order:
            bind_t, bind_q, bind_s = self.bind_local[name]
            track = action['tracks'].get(name)
            if track is None:
                pose[name] = (bind_t, bind_q, bind_s)
                continue
            times, channels = track
            lt = (self._sample_channel(times, channels['t'], t, False)
                  if 't' in channels else bind_t)
            lq = (math3d.quat_normalize(
                      self._sample_channel(times, channels['q'], t, True))
                  if 'q' in channels else bind_q)
            ls = (self._sample_channel(times, channels['s'], t, False)
                  if 's' in channels else bind_s)
            pose[name] = (lt, lq, ls)
        return pose

    @staticmethod
    def blend_local_poses(poses, weights):
        """Blend per-joint local poses; weights need not be normalized.

        Translations and scales blend linearly.  Quaternions are first
        aligned into one hemisphere (q and -q are the same rotation) and
        then blended as a normalized weighted sum, which is exact at the
        endpoints and well-behaved for cross-fades.
        """
        total = sum(weights)
        if total <= 0.0:
            raise ValueError('blend weights sum to zero')
        weights = [w / total for w in weights]
        out = {}
        for name in poses[0]:
            acc_t = [0.0, 0.0, 0.0]
            acc_s = [0.0, 0.0, 0.0]
            acc_q = None
            ref_q = poses[0][name][1]
            for pose, w in zip(poses, weights):
                lt, lq, ls = pose[name]
                for i in range(3):
                    acc_t[i] += w * lt[i]
                    acc_s[i] += w * ls[i]
                lq = math3d.quat_align(lq, ref_q)
                if acc_q is None:
                    acc_q = [w * lq[0], w * lq[1], w * lq[2], w * lq[3]]
                else:
                    for i in range(4):
                        acc_q[i] += w * lq[i]
            out[name] = (tuple(acc_t), math3d.quat_normalize(tuple(acc_q)),
                         tuple(acc_s))
        return out

    def blended_local_pose(self, specs):
        """Local pose for a blend of ``(action, time, weight)`` triples."""
        key = tuple((s[0], float(s[1]), float(s[2])) for s in specs)
        if self.cache_enabled and key in self._local_pose_cache:
            return self._local_pose_cache[key]
        if len(specs) == 1:
            pose = self.sample_action_local(specs[0][0], specs[0][1])
        else:
            poses = [self.sample_action_local(a, t) for a, t, _ in specs]
            weights = [w for _, _, w in specs]
            pose = self.blend_local_poses(poses, weights)
        if self.cache_enabled:
            self._local_pose_cache[key] = pose
        return pose

    # ------------------------------------------------------------------
    # World poses and skinning
    # ------------------------------------------------------------------
    def _world_from_local(self, local_pose):
        poses = {}
        for name in self.order:
            t, q, s = local_pose[name]
            local = math3d.trs_matrix(t, q, s)
            parent = self.joints[name]['parent']
            if parent is None:
                poses[name] = local
            else:
                poses[name] = math3d.mul(poses[parent], local)
        return poses

    def pose_world_blended(self, specs):
        """World transform of every joint for a blend spec."""
        key = tuple((s[0], float(s[1]), float(s[2])) for s in specs)
        if self.cache_enabled and key in self._skin_cache:
            cached = self._skin_cache[key]
            return cached[0]
        world = self._world_from_local(self.blended_local_pose(specs))
        skins = {name: math3d.mul(world[name], self.bind_inverse[name])
                 for name in self.order}
        if self.cache_enabled:
            self._skin_cache[key] = (world, skins)
        return world

    def skin_matrices_blended(self, specs):
        key = tuple((s[0], float(s[1]), float(s[2])) for s in specs)
        if self.cache_enabled:
            if key not in self._skin_cache:
                self.pose_world_blended(specs)
            return self._skin_cache[key][1]
        world = self._world_from_local(self.blended_local_pose(specs))
        return {name: math3d.mul(world[name], self.bind_inverse[name])
                for name in self.order}

    def joint_world_positions(self, specs):
        """World-space origin of every joint (for validation)."""
        world = self.pose_world_blended(specs)
        return {name: (m[3], m[7], m[11]) for name, m in world.items()}

    def skin_vertices_blended(self, vertices, specs):
        """Apply LBS for a blend spec and return deformed vertices."""
        if len(vertices) != len(self.weights):
            raise ValueError(
                'rig has %d weight entries but mesh has %d vertices'
                % (len(self.weights), len(vertices)))
        skins = self.skin_matrices_blended(specs)
        return _apply_skin(skins, self.weights, vertices)

    # ------------------------------------------------------------------
    # Legacy API (unchanged behaviour for the original bar rig)
    # ------------------------------------------------------------------
    def pose_world(self, frame):
        """Animated world transform of every joint at the given frame."""
        if self.animated_joint is None:
            raise ValueError('rig has no legacy animation record')
        angle = self.angles[frame % self.frame_count]
        rotation = math3d.rotation_axis(self.axis, angle)
        poses = {}
        for name in self.order:
            joint = self.joints[name]
            local = math3d.translation(*joint['position'])
            if name == self.animated_joint:
                # rotate about the joint's local origin: T * R
                local = math3d.mul(local, rotation)
            parent = joint['parent']
            if parent is None:
                poses[name] = local
            else:
                poses[name] = math3d.mul(poses[parent], local)
        return poses

    def skin_matrices(self, frame):
        poses = self.pose_world(frame)
        return {name: math3d.mul(poses[name], self.bind_inverse[name])
                for name in self.order}

    def skin_vertices(self, vertices, frame):
        """Apply LBS to a vertex list and return deformed (x, y, z) list."""
        if len(vertices) != len(self.weights):
            raise ValueError(
                'rig has %d weight entries but mesh has %d vertices'
                % (len(self.weights), len(vertices)))
        skins = self.skin_matrices(frame)
        return _apply_skin(skins, self.weights, vertices)


def _apply_skin(skins, weights, vertices):
    out = []
    for vertex, weight_list in zip(vertices, weights):
        acc = [0.0, 0.0, 0.0]
        wsum = 0.0
        for name, weight in weight_list:
            if weight == 0.0:
                continue
            p = math3d.transform(skins[name], vertex)
            acc[0] += weight * p[0]
            acc[1] += weight * p[1]
            acc[2] += weight * p[2]
            wsum += weight
        if wsum not in (0.0, 1.0):
            inv = 1.0 / wsum
            acc = [c * inv for c in acc]
        out.append((acc[0], acc[1], acc[2]))
    return out


def invert_rigid(m):
    """Inverse of a rigid (rotation + translation) 4x4 matrix."""
    # R is orthonormal in the upper-left 3x3, so R^-1 = R^T.
    r00, r01, r02, t0 = m[0], m[1], m[2], m[3]
    r10, r11, r12, t1 = m[4], m[5], m[6], m[7]
    r20, r21, r22, t2 = m[8], m[9], m[10], m[11]
    inv_t0 = -(r00 * t0 + r10 * t1 + r20 * t2)
    inv_t1 = -(r01 * t0 + r11 * t1 + r21 * t2)
    inv_t2 = -(r02 * t0 + r12 * t1 + r22 * t2)
    return [r00, r10, r20, inv_t0,
            r01, r11, r21, inv_t1,
            r02, r12, r22, inv_t2,
            0.0, 0.0, 0.0, 1.0]


def load_rig(path):
    return Rig(path)
