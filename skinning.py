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
    return Rig(path)
