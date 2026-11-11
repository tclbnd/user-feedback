"""Linear-blend skinning driven by rig.json (stdlib only).

The rig file declares a joint hierarchy, per-vertex skin weights and
animation keyframes (rotation of one joint around an axis).  For every
frame each joint's skinning matrix is

    skin_j = pose_world_j * inverse(bind_world_j)

and deformed vertices are the weighted sum

    v' = sum_j weight(v, j) * skin_j * v
"""

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
                'position': tuple(joint['position']),
            }
            self.order.append(name)

        # weights: per vertex, list of (joint_name, weight)
        self.weights = [
            [(entry[0], float(entry[1])) for entry in vertex_weights]
            for vertex_weights in data['weights']
        ]

        anim = data['animation']
        self.animated_joint = anim['joint']
        self.axis = tuple(anim['axis'])
        self.frame_count = int(anim['frames'])
        self.angles = [math.radians(float(a))
                       for a in anim['angle_degrees']]
        if len(self.angles) != self.frame_count:
            raise ValueError('animation frame count / angle list mismatch')

        # Bind-pose world transforms: chains of local translations.
        self.bind_world = {}
        for name in self.order:
            joint = self.joints[name]
            local = math3d.translation(*joint['position'])
            parent = joint['parent']
            if parent is None:
                self.bind_world[name] = local
            else:
                self.bind_world[name] = math3d.mul(
                    self.bind_world[parent], local)
        self.bind_inverse = {
            name: invert_rigid(self.bind_world[name]) for name in self.order
        }

    def pose_world(self, frame):
        """Animated world transform of every joint at the given frame."""
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
        out = []
        for vertex, weight_list in zip(vertices, self.weights):
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
    """Load a rig file.

    Format-1 files (the original ``animation`` block) return the legacy
    :class:`Rig` unchanged.  Format-2 files (``"format": 2`` with
    ``clips``) return a :class:`Skeleton` with multi-level hierarchy,
    non-uniform scale and multi-clip blending.
    """
    import json as _json
    with open(path, 'r', encoding='utf-8') as fh:
        data = _json.load(fh)
    if data.get('format') == 2 or 'clips' in data:
        return Skeleton(data)
    return Rig(path)


# ----------------------------------------------------------------------
# Format 2: multi-level skeletons, TRS channels, quaternion cross-fades
# ----------------------------------------------------------------------
class AnimationClip:
    """One named action: per-joint translation / rotation / scale tracks.

    Rotation tracks hold unit quaternions (x, y, z, w); either hemisphere
    is legal because -q describes the same rotation.  Interpolation
    canonicalizes each key against the previous one so equivalent
    representations never cause a long-way spin.
    """

    def __init__(self, name, data):
        self.name = name
        self.duration = float(data['duration'])
        self.channels = {}
        for joint_name, joint_data in data.get('joints', {}).items():
            self.channels[joint_name] = {
                kind: (list(track['times']),
                       [tuple(v) for v in track['values']])
                for kind, track in joint_data.items()
            }

    @staticmethod
    def _find_span(times, t):
        """Bracket time ``t``; returns (i, alpha). Loops over duration."""
        n = len(times)
        if n == 1:
            return 0, 0.0
        if t <= times[0]:
            return 0, 0.0
        if t >= times[-1]:
            return n - 2, 1.0
        lo, hi = 0, n - 1
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if times[mid] <= t:
                lo = mid
            else:
                hi = mid
        span = times[hi] - times[lo]
        alpha = 0.0 if span == 0.0 else (t - times[lo]) / span
        return lo, alpha

    def sample_joint(self, joint_name, time, loop=True):
        """Local TRS of one joint at ``time``; None keys mean 'not animated'."""
        if loop and self.duration > 0.0:
            time = time % self.duration
        else:
            time = max(0.0, min(time, self.duration))
        chans = self.channels.get(joint_name)
        if chans is None:
            return None
        out = {}
        if 'translation' in chans:
            times, values = chans['translation']
            i, a = self._find_span(times, time)
            out['translation'] = math3d.vlerp(values[i], values[i + 1], a)
        if 'scale' in chans:
            times, values = chans['scale']
            i, a = self._find_span(times, time)
            out['scale'] = math3d.vlerp(values[i], values[i + 1], a)
        if 'rotation' in chans:
            times, values = chans['rotation']
            i, a = self._find_span(times, time)
            q0 = math3d.quat_canonical(values[i])
            q1 = values[i + 1]
            # hemisphere-correct the next key against the current one
            if math3d.quat_dot(q0, q1) < 0.0:
                q1 = (-q1[0], -q1[1], -q1[2], -q1[3])
            out['rotation'] = math3d.quat_slerp(q0, q1, a)
        return out


class Pose:
    """A frozen set of local TRS values plus derived world/skin matrices.

    Built fresh for every evaluation; the skeleton keeps no pose state,
    so rendering one time can never pollute the next frame.
    """

    def __init__(self, local_trs, skeleton):
        self.local_trs = local_trs
        self.skeleton = skeleton
        self.local_matrix = {}
        self.world_matrix = {}
        for name in skeleton.order:
            trs = local_trs[name]
            self.local_matrix[name] = math3d.compose(
                trs['translation'], trs['rotation'], trs['scale'])
        for name in skeleton.order:
            parent = skeleton.joints[name]['parent']
            local = self.local_matrix[name]
            if parent is None:
                self.world_matrix[name] = local
            else:
                self.world_matrix[name] = \
                    math3d.mul(self.world_matrix[parent], local)
        self.skin_matrix = {
            name: math3d.mul(self.world_matrix[name],
                             skeleton.bind_inverse[name])
            for name in skeleton.order}

    def joint_world_position(self, name):
        return math3d.transform(self.world_matrix[name], (0.0, 0.0, 0.0))


class Skeleton:
    """Format-2 rig: hierarchy + bind TRS + named clips + LBS skinning."""

    def __init__(self, data):
        self.joints = {}
        self.order = []
        for joint in data['joints']:
            name = joint['name']
            self.joints[name] = {
                'parent': joint['parent'],
                'position': tuple(joint.get('position', (0.0, 0.0, 0.0))),
                'rotation': math3d.quat_normalize(
                    tuple(joint.get('rotation', (0.0, 0.0, 0.0, 1.0)))),
                'scale': tuple(joint.get('scale', (1.0, 1.0, 1.0))),
            }
            self.order.append(name)

        self.weights = [
            [(entry[0], float(entry[1])) for entry in vertex_weights]
            for vertex_weights in data['weights']
        ]

        self.clips = {
            name: AnimationClip(name, clip_data)
            for name, clip_data in data.get('clips', {}).items()}

        # Bind-pose world matrices through the full TRS chain.  A general
        # inverse is required because bind transforms may carry
        # non-uniform scale (invert_rigid only handles rotation+translation).
        self.bind_world = {}
        self.bind_local = {}
        for name in self.order:
            joint = self.joints[name]
            local = math3d.compose(joint['position'], joint['rotation'],
                                   joint['scale'])
            self.bind_local[name] = local
            parent = joint['parent']
            if parent is None:
                self.bind_world[name] = local
            else:
                self.bind_world[name] = math3d.mul(
                    self.bind_world[parent], local)
        self.bind_inverse = {
            name: math3d.inverse(self.bind_world[name])
            for name in self.order}

    # ------------------------------------------------------------------
    # Pose evaluation (pure: same inputs always give the same outputs)
    # ------------------------------------------------------------------
    def _default_local(self, name):
        joint = self.joints[name]
        return {'translation': joint['position'],
                'rotation': joint['rotation'],
                'scale': joint['scale']}

    def evaluate_clip(self, clip_name, time, loop=True):
        """Evaluate one clip into a full :class:`Pose`."""
        clip = self.clips[clip_name]
        local_trs = {}
        for name in self.order:
            trs = self._default_local(name)
            sampled = clip.sample_joint(name, time, loop=loop)
            if sampled:
                trs.update(sampled)
            local_trs[name] = trs
        return Pose(local_trs, self)

    def evaluate_blend(self, layers, loop=True):
        """Cross-fade several clips by blending *local* joint poses.

        ``layers`` is a list of ``(clip_name, time, weight)`` tuples.
        Translations and non-uniform scales are weighted lerps; rotations
        are hemisphere-corrected against the first contributing quaternion,
        weighted-summed and re-normalized.  Weights are normalized to sum 1,
        so a single active layer reproduces that clip exactly.
        """
        total_weight = sum(w for _, _, w in layers)
        if total_weight <= 0.0:
            raise ValueError('blend layers must have a positive total weight')
        sampled_layers = []
        for clip_name, time, weight in layers:
            if weight <= 0.0:
                continue
            clip = self.clips[clip_name]
            sampled_layers.append(
                (clip, time, weight / total_weight))

        local_trs = {}
        for name in self.order:
            base = self._default_local(name)
            t_acc = [0.0, 0.0, 0.0]
            s_acc = [0.0, 0.0, 0.0]
            q_acc = [0.0, 0.0, 0.0, 0.0]
            ref_q = None
            wsum = 0.0
            for clip, time, w in sampled_layers:
                sampled = clip.sample_joint(name, time, loop=loop)
                trs = sampled if sampled else {}
                t = trs.get('translation', base['translation'])
                s = trs.get('scale', base['scale'])
                q = math3d.quat_normalize(
                    trs.get('rotation', base['rotation']))
                if ref_q is None:
                    ref_q = q
                elif math3d.quat_dot(ref_q, q) < 0.0:
                    # equivalent quaternion, opposite hemisphere
                    q = (-q[0], -q[1], -q[2], -q[3])
                for k in range(3):
                    t_acc[k] += w * t[k]
                    s_acc[k] += w * s[k]
                for k in range(4):
                    q_acc[k] += w * q[k]
                wsum += w
            if wsum != 1.0:
                inv = 1.0 / wsum
                t_acc = [c * inv for c in t_acc]
                s_acc = [c * inv for c in s_acc]
                q_acc = [c * inv for c in q_acc]
            local_trs[name] = {
                'translation': tuple(t_acc),
                'rotation': math3d.quat_normalize(tuple(q_acc)),
                'scale': tuple(s_acc)}
        return Pose(local_trs, self)

    # ------------------------------------------------------------------
    # Linear-blend skinning
    # ------------------------------------------------------------------
    def skin_vertices_pose(self, vertices, pose):
        """Deform ``vertices`` with an explicit Pose (shared geometry path)."""
        if len(vertices) != len(self.weights):
            raise ValueError(
                'rig has %d weight entries but mesh has %d vertices'
                % (len(self.weights), len(vertices)))
        skins = pose.skin_matrix
        out = []
        for vertex, weight_list in zip(vertices, self.weights):
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

    def skin_vertices(self, vertices, pose):
        """Backwards-friendly entry point: accepts a Pose directly."""
        if not isinstance(pose, Pose):
            raise TypeError(
                'Skeleton.skin_vertices expects a Pose from evaluate_*')
        return self.skin_vertices_pose(vertices, pose)
