# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: SMPL motion (EDGE and other music-to-dance or mocap models) -> a BVH the retargeter maps by name.
#
#   python autorig/cli/run.py smpl-to-bvh <motion.pkl|.npz> <out.bvh> [--fps 30] [--up z|y] [--arms-out DEG]
#                                         [--feet-keep 0..1] [--leg-twist-keep 0..1] [--finger-curl DEG]
#   blender -b --python autorig/steps/smpl_to_bvh.py -- <same arguments>
#
# The input holds smpl_poses (T, 72 axis-angle: 24 joints, root first) and smpl_trans (T, 3), and may hold full_pose
# (T, 24, 3 joint positions), as EDGE's test.py --save_motions writes. The BVH uses Mixamo's bone names (the
# workbench humanoid's), SMPL's neutral rest in centimetres, Y up, facing +Z. What it takes care of:
#   - the up axis: EDGE works Z up (its data turns AIST++'s root +90 deg about X); --up y for Y-up SMPL data;
#   - the floor: with full_pose, the dance is lowered so its feet stand on 0 (the low 5% of each frame's lowest
#     ankle or toe), since EDGE's floor sits near z 0.94;
#   - the root's rest OFFSET is SMPL's standing pelvis height: the retargeter adds the root's motion to the
#     character's own hip height relative to it, so an OFFSET of 0 doubles her height;
#   - --arms-out lifts both upper arms out from the sides (SMPL dancers wear tight clothes; a robe needs room);
#   - --feet-keep keeps that share of the ankle rotation and holds the toes straight; --leg-twist-keep keeps that
#     share of the turn along the thigh and shin (the swing stays), for boots under a robe;
#   - --finger-curl adds five fingers per hand (SMPL has none) held in a relaxed curl of about DEG per joint, so a
#     rig with digits does not dance with its hands flat and spread as in its T-pose.
# Runs in Blender's Python (mathutils): nothing to install.
import sys, os, pickle
import numpy as np
from mathutils import Matrix, Quaternion, Vector

NAMES = ["Hips", "LeftUpLeg", "RightUpLeg", "Spine", "LeftLeg", "RightLeg", "Spine1", "LeftFoot", "RightFoot",
         "Spine2", "LeftToeBase", "RightToeBase", "Neck", "LeftShoulder", "RightShoulder", "Head", "LeftArm",
         "RightArm", "LeftForeArm", "RightForeArm", "LeftHand", "RightHand", "LeftHandEnd", "RightHandEnd"]
PARENTS = [-1, 0, 0, 0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 9, 9, 12, 13, 14, 16, 17, 18, 19, 20, 21]
OFFSETS = np.array([   # SMPL's neutral rest, metres, Y up, facing +Z, its left = +X
    [0.0, 0.0, 0.0], [0.05858135, -0.08228004, -0.01766408], [-0.06030973, -0.09051332, -0.01354254],
    [0.00443945, 0.12440352, -0.03838522], [0.04345142, -0.38646945, 0.008037], [-0.04325663, -0.38368791, -0.00484304],
    [0.00448844, 0.1379564, 0.02682033], [-0.01479032, -0.42687458, -0.037428], [0.01905555, -0.4200455, -0.03456167],
    [-0.00226458, 0.05603239, 0.00285505], [0.04105436, -0.06028581, 0.12204243], [-0.03483987, -0.06210566, 0.13032329],
    [-0.0133902, 0.21163553, -0.03346758], [0.07170245, 0.11399969, -0.01889817], [-0.08295366, 0.11247234, -0.02370739],
    [0.01011321, 0.08893734, 0.05040987], [0.12292141, 0.04520509, -0.019046], [-0.11322832, 0.04685326, -0.00847207],
    [0.2553319, -0.01564902, -0.02294649], [-0.26012748, -0.01436928, -0.03126873], [0.26570925, 0.01269811, -0.00737473],
    [-0.26910836, 0.00679372, -0.00602676], [0.08669055, -0.01063603, -0.01559429], [-0.0887537, -0.00865157, -0.01010708]])
END = {10: [0, 0, 0.05], 11: [0, 0, 0.05], 15: [0, 0.15, 0.02], 22: [0.08, 0, 0], 23: [-0.08, 0, 0]}
REST_PELVIS = -(OFFSETS[1][1] + OFFSETS[4][1] + OFFSETS[7][1] + OFFSETS[10][1])
# --finger-curl: five fingers per hand (the workbench's digits names), SMPL frame (palm down, thumb forward +Z), each
# finger held in a relaxed curl. (knuckle across the palm, segment lengths, share of the curl)
FINGERS = [("Thumb", 0.035, 0.032, (0.030, 0.025, 0.020), 0.45), ("Index", 0.095, 0.020, (0.035, 0.025, 0.020), 0.75),
           ("Middle", 0.098, 0.000, (0.040, 0.028, 0.022), 0.9), ("Ring", 0.093, -0.018, (0.036, 0.026, 0.020), 1.0),
           ("Pinky", 0.085, -0.034, (0.028, 0.020, 0.017), 1.15)]
CM = 100.0


def q_of(rv):
    v = Vector(rv); a = v.length
    return Quaternion(v.normalized(), a) if a > 1e-12 else Quaternion()


def rv_of(q):
    q = q.normalized()
    if q.w < 0: q = -q
    ax, an = q.to_axis_angle()
    return np.array(ax) * an


def load(path):
    if path.endswith(".npz"):
        d = dict(np.load(path, allow_pickle=True))
    else:
        with open(path, "rb") as fh:
            d = pickle.load(fh)
    q = np.asarray(d["smpl_poses"], dtype=np.float64).reshape(-1, 24, 3)
    t = np.asarray(d["smpl_trans"], dtype=np.float64).reshape(-1, 3).copy()
    fp = np.asarray(d["full_pose"]) if "full_pose" in d else None
    return q, t, fp


def convert(src, dst, fps=30, up="z", arms_out=0.0, feet_keep=1.0, leg_twist_keep=1.0, finger_curl=0.0):
    q, trans, fp = load(src)
    if fp is not None:
        ax = 2 if up == "z" else 1
        floor = float(np.percentile(fp[:, [7, 8, 10, 11], ax].min(axis=1), 5))
        trans[:, ax] -= floor
        print("SMPL floor at %.3f: lowered onto 0" % floor)
    to_yup = Quaternion((1, 0, 0), -np.pi / 2) if up == "z" else Quaternion()
    T = len(q)
    if arms_out:
        for j, sgn in ((16, 1), (17, -1)):            # turn about the body's forward axis (Z) in the parent's frame
            r = Quaternion((0, 0, 1), np.radians(sgn * arms_out))
            for t in range(T): q[t, j] = rv_of(r @ q_of(q[t, j]))
    if leg_twist_keep < 1:
        for j, child in ((1, 4), (2, 5), (4, 7), (5, 8)):
            axis = Vector(OFFSETS[child]).normalized()
            for t in range(T):
                qq = q_of(q[t, j]); p = Vector(qq[1:]).dot(axis) * axis
                tw = Quaternion((qq.w, p.x, p.y, p.z)).normalized() if (qq.w ** 2 + p.length_squared) > 1e-12 else Quaternion()
                swing = qq @ tw.inverted()
                tw_ax, tw_an = tw.to_axis_angle()
                q[t, j] = rv_of(swing @ Quaternion(tw_ax, tw_an * leg_twist_keep))
    if feet_keep < 1:
        q[:, [7, 8]] *= feet_keep
        q[:, [10, 11]] = 0
    names, parents, offsets = list(NAMES), list(PARENTS), [np.array(o) for o in OFFSETS]
    finger_rot = {}
    if finger_curl:
        for side, hand, sx in (("Left", 20, 1.0), ("Right", 21, -1.0)):
            for fname, along, across, segs, share in FINGERS:
                parent = hand
                for k in range(3):
                    names.append("%sHand%s%d" % (side, fname, k + 1)); parents.append(parent)
                    offsets.append(np.array([sx * along, -0.01, across]) if k == 0 else np.array([sx * segs[k - 1], 0.0, 0.0]))
                    j = len(names) - 1
                    ang = np.radians(finger_curl * share * (0.6 if k == 0 else 1.0))
                    if fname == "Thumb":   # the thumb tucks in toward the palm, across it
                        finger_rot[j] = Quaternion((0, 1, 0), sx * ang * 0.8) @ Quaternion((0, 0, 1), -sx * ang * 0.5)
                    else:                  # fingers curl down toward the palm (-Y): about +Z, mirrored for the right
                        finger_rot[j] = Quaternion((0, 0, 1), -sx * ang)
                    parent = j
    NJ = len(names)
    kids = {i: [j for j in range(NJ) if parents[j] == i] for i in range(NJ)}
    lines = ["HIERARCHY"]

    def write(j, depth):
        pad = "  " * depth
        o = (offsets[j] if j else np.array([0.0, REST_PELVIS, 0.0])) * CM
        lines.append("%s%s %s" % (pad, "ROOT" if j == 0 else "JOINT", names[j])); lines.append(pad + "{")
        lines.append("%s  OFFSET %.5f %.5f %.5f" % (pad, *o))
        lines.append(pad + ("  CHANNELS 6 Xposition Yposition Zposition Zrotation Xrotation Yrotation" if j == 0
                            else "  CHANNELS 3 Zrotation Xrotation Yrotation"))
        for k in kids[j]: write(k, depth + 1)
        if not kids[j]:
            e = np.array(END.get(j, [0.02 * (1 if "Left" in names[j] else -1), 0, 0] if j >= 24 else [0, 0.05, 0])) * CM
            lines.append("%s  End Site\n%s  {\n%s    OFFSET %.5f %.5f %.5f\n%s  }" % (pad, pad, pad, *e, pad))
        lines.append(pad + "}")

    write(0, 0)
    order = []
    def walk(j):
        order.append(j)
        for k in kids[j]: walk(k)
    walk(0)
    lines += ["MOTION", "Frames: %d" % T, "Frame Time: %.8f" % (1.0 / fps)]
    for t in range(T):
        pos = to_yup @ Vector(trans[t])
        row = [pos.x * CM, pos.y * CM, pos.z * CM]
        for j in order:
            rot = (to_yup @ q_of(q[t, 0])) if j == 0 else (finger_rot[j] if j >= 24 else q_of(q[t, j]))
            e = rot.to_matrix().to_euler("YXZ")       # BVH "Z X Y" (M = Rz Rx Ry) is Blender's YXZ order
            row += [np.degrees(e.z), np.degrees(e.x), np.degrees(e.y)]
        lines.append(" ".join("%.4f" % v for v in row))
    with open(dst, "w") as fh:
        fh.write("\n".join(lines) + "\n")
    print("SMPL_TO_BVH wrote %s: %d frames at %d fps (%.1f s)" % (dst, T, fps, T / fps))
    return 0


def main(argv):
    a = argv
    if len(a) < 2: sys.exit("usage: smpl_to_bvh.py <motion.pkl|.npz> <out.bvh> [--fps N] [--up z|y] [--arms-out DEG] "
                            "[--feet-keep F] [--leg-twist-keep F]")
    opt = lambda n, d: type(d)(a[a.index(n) + 1]) if n in a else d
    return convert(a[0], a[1], fps=opt("--fps", 30), up=opt("--up", "z"), arms_out=opt("--arms-out", 0.0),
                   feet_keep=opt("--feet-keep", 1.0), leg_twist_keep=opt("--leg-twist-keep", 1.0),
                   finger_curl=opt("--finger-curl", 0.0))


if __name__ == "__main__":
    sys.exit(main(sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]))
