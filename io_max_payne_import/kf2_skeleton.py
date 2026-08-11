"""Skeletal animation support for Max Payne KF2/KFS/SKD models.

The data for one character is split across three files, and none of them is
sufficient alone:

  *.KFS  mesh geometry + materials (node name identifies the mesh)
  *.SKD  SKIN chunk: skin_object_names, skeleton_object_names (bone order),
         and per-vertex bone index/weight pairs -- but NO bone transforms
  *.KF2  one KEYFRAME_ANIMATION per bone. Each carries parent_name (giving
         the skeleton hierarchy) and per-frame transforms (giving the bone
         placement). The first keyframe serves as the rest pose.

So the bone hierarchy and rest pose come from an animation file, not from the
skin or the mesh. Importing a character requires all three together.
"""

import collections
import traceback

import bpy
from mathutils import Vector, Matrix


A3 = Matrix(((-1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)))


def conv_matrix(rows, scale=1.0):
    """Convert a game-space row-vector transform into a Blender matrix."""
    if not rows or len(rows) < 4:
        return Matrix.Identity(4)
    try:
        L = Matrix((
            (rows[0][0], rows[0][1], rows[0][2]),
            (rows[1][0], rows[1][1], rows[1][2]),
            (rows[2][0], rows[2][1], rows[2][2]),
        ))
        t = Vector((rows[3][0], rows[3][1], rows[3][2]))
        m = (A3 @ L.transposed() @ A3.transposed()).to_4x4()
        m.translation = (A3 @ t) * scale
        return m
    except Exception:
        return Matrix.Identity(4)


def build_bone_table(keyframe_animations, mesh_nodes=None, rest_animations=None):
    """Return {bone_name: {parent, rest_rows, keys, fps, looping}}.

    Max Payne separates the bind/rest pose from motion. The skeleton is shared
    (Data/database/skeletons/default_skeleton) and a dedicated pose file --
    Widepose.kf2 for the standard human rig -- supplies the neutral pose the
    skin weights were authored against. Ordinary animations such as Stand.kf2
    or 2_DodgeBackwardLeft.kf2 supply motion only.

    rest_animations, when given, is used solely for bone placement; the
    hierarchy and rest transforms come from it, while keyframe_animations
    provides the keys. Falling back to an animation's first frame as the rest
    pose skews the rig, because that frame is a posed frame, not the bind pose.
    """
    table = {}

    def ingest(anims, is_rest):
        for anim in (anims or []):
            inner = getattr(anim, "animation", None)
            name = getattr(inner, "object_name", None) if inner else None
            if not name:
                continue
            keys = []
            for kf in (getattr(anim, "keyframes", None) or []):
                rows = getattr(kf, "transform", None)
                if rows:
                    keys.append((int(getattr(kf, "frame_id", 0)), rows))
            keys.sort(key=lambda k: k[0])
            entry = table.setdefault(name, {"parent": "", "rest_rows": None,
                                            "keys": [], "fps": 0,
                                            "looping": False,
                                            "rest_from_pose": False})
            parent = getattr(anim, "parent_name", "") or ""
            if parent:
                entry["parent"] = parent
            if is_rest:
                if keys:
                    entry["rest_rows"] = keys[0][1]
                    entry["rest_from_pose"] = True
            else:
                if keys:
                    entry["keys"] = keys
                    if entry["rest_rows"] is None:
                        entry["rest_rows"] = keys[0][1]
                entry["fps"] = int(getattr(inner, "fps", 0) or 0)
                entry["looping"] = bool(getattr(inner, "is_looping", False))

    # Rest pose first so hierarchy is established from the canonical skeleton.
    ingest(rest_animations, True)
    ingest(keyframe_animations, False)

    for name, (parent, rows) in (mesh_nodes or {}).items():
        entry = table.setdefault(name, {"parent": "", "rest_rows": None,
                                        "keys": [], "fps": 0, "looping": False,
                                        "rest_from_pose": False})
        if not entry["parent"]:
            entry["parent"] = parent
        if entry["rest_rows"] is None:
            entry["rest_rows"] = rows

    return table


_pose_cache = {}


def clear_pose_cache():
    _pose_cache.clear()


def _interp_local(entry, frame_id):
    """Local transform of a bone at a frame, interpolated between its keys.

    Bones are keyed sparsely and independently -- in Run.kf2 the Neck has 2
    keys, the Pelvis 16 and Hand-R 28 across the same 40-frame span. Holding
    the previous key (step interpolation) makes sparsely-keyed bones jump
    between poses while densely-keyed ones move smoothly, which reads as a
    jerky animation. Interpolating here reproduces the smooth motion the game
    plays, and it must happen at this level because every bone is then baked to
    a key on every frame, leaving Blender's own interpolation nothing to do.
    """
    keys = entry["keys"]
    if not keys:
        return entry["rest_rows"]
    if len(keys) == 1 or frame_id <= keys[0][0]:
        return keys[0][1]
    if frame_id >= keys[-1][0]:
        return keys[-1][1]

    lo = keys[0]
    hi = keys[-1]
    for i in range(len(keys) - 1):
        if keys[i][0] <= frame_id <= keys[i + 1][0]:
            lo, hi = keys[i], keys[i + 1]
            break

    span = hi[0] - lo[0]
    if span <= 0:
        return lo[1]
    t = (frame_id - lo[0]) / float(span)

    a, b = lo[1], hi[1]
    m_a = Matrix(((a[0][0], a[0][1], a[0][2]),
                  (a[1][0], a[1][1], a[1][2]),
                  (a[2][0], a[2][1], a[2][2]))).transposed()
    m_b = Matrix(((b[0][0], b[0][1], b[0][2]),
                  (b[1][0], b[1][1], b[1][2]),
                  (b[2][0], b[2][1], b[2][2]))).transposed()

    # Rotations must be slerped as quaternions; lerping matrix components
    # would shear the bone and distort the mesh.
    q = m_a.to_quaternion().slerp(m_b.to_quaternion(), t)
    r = q.to_matrix().transposed()

    ta, tb = a[3], b[3]
    trans = [ta[i] + (tb[i] - ta[i]) * t for i in range(3)]

    return [[r[0][0], r[0][1], r[0][2]],
            [r[1][0], r[1][1], r[1][2]],
            [r[2][0], r[2][1], r[2][2]],
            trans]


def world_pose_matrix(name, table, scale, frame_id, _seen=None):
    """World transform of a bone at a frame, interpolated between its own keys
    and accumulated up the parent chain."""
    cache_key = (name, frame_id, scale)
    cached = _pose_cache.get(cache_key)
    if cached is not None:
        return cached
    if _seen is None:
        _seen = set()
    if name not in table or name in _seen:
        return Matrix.Identity(4)
    _seen = _seen | {name}

    entry = table[name]
    local = conv_matrix(_interp_local(entry, frame_id), scale)

    parent = entry.get("parent") or ""
    if parent and parent in table:
        result = world_pose_matrix(parent, table, scale, frame_id, _seen) @ local
    else:
        result = local
    _pose_cache[cache_key] = result
    return result


def world_rest_matrix(name, table, scale, _seen=None):
    if _seen is None:
        _seen = set()
    if name not in table or name in _seen:
        return Matrix.Identity(4)
    _seen = _seen | {name}
    entry = table[name]
    local = conv_matrix(entry["rest_rows"], scale)
    parent = entry.get("parent") or ""
    if parent and parent in table:
        return world_rest_matrix(parent, table, scale, _seen) @ local
    return local


def build_armature(bone_names, table, scale, collection, name, log):
    arm_data = bpy.data.armatures.new(name)
    arm_obj = bpy.data.objects.new(name, arm_data)
    collection.objects.link(arm_obj)

    view_layer = bpy.context.view_layer
    prev_active = view_layer.objects.active

    # Entering EDIT mode requires a clean context: the operator's poll fails if
    # something else is mid-edit, if nothing is active, or if the target object
    # is not selectable in the current view layer. Force OBJECT mode first and
    # make the armature the sole active+selected object.
    try:
        if prev_active is not None and getattr(prev_active, "mode", "OBJECT") != 'OBJECT':
            bpy.ops.object.mode_set(mode='OBJECT')
    except Exception:
        pass
    try:
        for ob in bpy.context.selected_objects:
            ob.select_set(False)
    except Exception:
        pass
    try:
        arm_obj.select_set(True)
    except Exception:
        pass
    view_layer.objects.active = arm_obj

    missing = []
    entered_edit = False
    try:
        bpy.ops.object.mode_set(mode='EDIT')
        entered_edit = True
    except Exception as e:
        log.write("  could not enter EDIT mode to build bones: %s" % e)
        log.write("  (armature created but has no bones)")
        return arm_obj

    try:
        edit_bones = {}
        for bname in bone_names:
            if bname not in table or table[bname]["rest_rows"] is None:
                missing.append(bname)
            wm = world_rest_matrix(bname, table, scale)
            eb = arm_data.edit_bones.new(bname)
            head = wm.translation.copy()
            direction = wm.to_3x3() @ Vector((0.0, 0.0, 1.0))
            if direction.length < 1e-9:
                direction = Vector((0.0, 0.0, 1.0))
            direction.normalize()
            eb.head = head
            # Provisional tail; refined below once all heads are known.
            eb.tail = head + direction * max(0.04 * scale, 0.005)
            edit_bones[bname] = eb

        # The format gives bones no length, only a position and orientation,
        # so tails have to be derived. Real bone spans here run 0.05-0.5, so a
        # fixed tiny tail would render the whole rig as unusable stubs.
        children = {}
        for bname in bone_names:
            parent = table.get(bname, {}).get("parent") or ""
            if parent in edit_bones and parent != bname:
                children.setdefault(parent, []).append(bname)

        heads = {b: edit_bones[b].head.copy() for b in edit_bones}
        axes = {}
        for bname in bone_names:
            wm = world_rest_matrix(bname, table, scale)
            ax = wm.to_3x3() @ Vector((0.0, 0.0, 1.0))
            if ax.length < 1e-9:
                ax = Vector((0.0, 0.0, 1.0))
            axes[bname] = ax.normalized()

        for bname in bone_names:
            eb = edit_bones[bname]
            kids = children.get(bname, [])
            if len(kids) == 1:
                target = heads[kids[0]]
                if (target - eb.head).length > 1e-5:
                    eb.tail = target
                    continue
            elif len(kids) > 1:
                # Point at whichever child best follows this bone's own axis,
                # which keeps spines and pelvises oriented sensibly instead of
                # aiming at an arbitrary limb.
                best, best_dot = None, -2.0
                for k in kids:
                    d = heads[k] - eb.head
                    if d.length < 1e-6:
                        continue
                    dot = d.normalized().dot(axes[bname])
                    if dot > best_dot:
                        best, best_dot = heads[k], dot
                if best is not None and (best - eb.head).length > 1e-5:
                    eb.tail = best
                    continue

            # Leaf bone: match the length of the bone that feeds it.
            parent = table.get(bname, {}).get("parent") or ""
            length = 0.1 * scale
            if parent in heads:
                d = (eb.head - heads[parent]).length
                if d > 1e-5:
                    length = d * 0.5
            eb.tail = eb.head + axes[bname] * max(length, 0.01 * scale)

        for bname in bone_names:
            parent = table.get(bname, {}).get("parent") or ""
            if parent in edit_bones and parent != bname:
                edit_bones[bname].parent = edit_bones[parent]
    finally:
        if entered_edit:
            try:
                bpy.ops.object.mode_set(mode='OBJECT')
            except Exception:
                pass
        try:
            view_layer.objects.active = prev_active
        except Exception:
            pass

    # Persist each bone's GAME rest transform on the armature. Animations
    # loaded later need it to compute their delta from the bind pose; without
    # it they would fall back to their own first frame, which is a posed frame
    # and skews the whole rig.
    stored = 0
    for bname in bone_names:
        wm = world_rest_matrix(bname, table, scale)
        flat = []
        for row in wm:
            flat.extend(float(c) for c in row)
        arm_obj["mp_rest_" + bname] = flat
        stored += 1
    arm_obj["mp_rest_scale"] = float(scale)
    log.write("  stored game rest transforms for %i bones (used by later "
              "animation imports)" % stored)

    if missing:
        log.write("  WARNING: %i bone(s) had no transform data (no matching "
                  "animation chunk); placed at origin: %s"
                  % (len(missing), ", ".join(missing[:12])))
    log.write("  armature '%s': %i bones" % (name, len(bone_names)))
    return arm_obj


def apply_skin_weights(obj, skin, bone_names, log):
    if obj is None:
        return 0
    groups = {}
    for bi, bname in enumerate(bone_names):
        groups[bi] = obj.vertex_groups.get(bname) or obj.vertex_groups.new(name=bname)

    nverts = len(obj.data.vertices)
    assigned = 0
    out_of_range = 0
    for sv in (getattr(skin, "skin_vertices", None) or []):
        vidx = getattr(sv, "vertex_index", None)
        if vidx is None or vidx >= nverts:
            out_of_range += 1
            continue
        indices = getattr(sv, "vertex_bone_indices", None) or []
        weights = getattr(sv, "vertex_weights", None) or []
        for bi, w in zip(indices, weights):
            grp = groups.get(bi)
            if grp is not None and w:
                grp.add([vidx], float(w), 'REPLACE')
                assigned += 1

    if out_of_range:
        log.write("  WARNING: %i skin vertex indices exceeded the mesh vertex count"
                  % out_of_range)
    log.write("  skin weights: %i assignments across %i vertices" % (assigned, nverts))
    return assigned


def bind_mesh_to_armature(obj, arm_obj):
    """Attach a mesh to an armature so it deforms with the bones.

    The Armature *modifier* is what actually deforms the mesh -- vertex groups
    alone do nothing. Parenting is cosmetic by comparison, so the modifier is
    set up first and independently of it."""
    if obj is None or arm_obj is None:
        return

    mod = None
    for m in obj.modifiers:
        if m.type == 'ARMATURE':
            mod = m
            break
    if mod is None:
        mod = obj.modifiers.new(name="Armature", type='ARMATURE')
    mod.object = arm_obj
    mod.use_vertex_groups = True

    try:
        world = obj.matrix_world.copy()
        obj.parent = arm_obj
        obj.parent_type = 'OBJECT'
        obj.matrix_parent_inverse = arm_obj.matrix_world.inverted_safe()
        obj.matrix_world = world
    except Exception:
        # Parenting is optional; the modifier already drives deformation.
        pass


def stored_rest_matrix(arm_obj, bname):
    """Read back a game rest transform saved by build_armature."""
    flat = arm_obj.get("mp_rest_" + bname)
    if flat is None:
        return None
    try:
        vals = list(flat)
        if len(vals) != 16:
            return None
        return Matrix((vals[0:4], vals[4:8], vals[8:12], vals[12:16]))
    except Exception:
        return None


def apply_animation(arm_obj, bone_names, table, scale, action_name, log,
                    use_stored_rest=False):
    if arm_obj is None:
        return 0, 0

    clear_pose_cache()
    action = bpy.data.actions.new(action_name)
    if arm_obj.animation_data is None:
        arm_obj.animation_data_create()
    arm_obj.animation_data.action = action

    pose_bones = arm_obj.pose.bones
    data_bones = arm_obj.data.bones

    animated = 0
    max_frame = 0
    fps_values = []
    rest_world_cache = {}
    if use_stored_rest:
        found = 0
        for bname in bone_names:
            m = stored_rest_matrix(arm_obj, bname)
            if m is not None:
                rest_world_cache[bname] = m
                found += 1
        if found:
            log.write("  using the armature's stored bind pose for %i bones" % found)
        else:
            log.write("  WARNING: this armature has no stored bind pose; falling back "
                      "to each animation's first frame, which can skew the rig. "
                      "Re-import the character with its pose file (e.g. Widepose.kf2).")

    # Bones must be posed parents-first: setting pose_bone.matrix reads the
    # parent's current pose, so a child evaluated before its parent would be
    # placed against a stale parent transform.
    ordered = []
    seen = set()

    def add_in_order(bname):
        if bname in seen or bname not in table:
            return
        parent = table[bname].get("parent") or ""
        if parent and parent in table and parent != bname:
            add_in_order(parent)
        seen.add(bname)
        ordered.append(bname)

    for bname in bone_names:
        add_in_order(bname)

    all_frames = sorted({f for b in ordered
                         for f, _ in table.get(b, {}).get("keys", [])})
    if not all_frames:
        log.write("  no keyframes present; skipping animation")
        return 0, 0

    keyed_bones = set()
    prev_quat = {}

    def delta_for(bname, frame_id):
        """Movement of a bone away from the game's bind pose, in armature space."""
        pose_world = world_pose_matrix(bname, table, scale, frame_id)
        rest_world = rest_world_cache.get(bname)
        if rest_world is None:
            rest_world = world_rest_matrix(bname, table, scale)
            rest_world_cache[bname] = rest_world
        return pose_world @ rest_world.inverted_safe()

    for frame_id in all_frames:
        for bname in ordered:
            entry = table.get(bname)
            if not entry or not entry["keys"]:
                continue
            pb = pose_bones.get(bname)
            db = data_bones.get(bname)
            if pb is None or db is None:
                continue
            if entry["fps"]:
                fps_values.append(entry["fps"])

            # Blender evaluates a pose bone as
            #     pose = parent_pose @ parent_rest^-1 @ rest @ basis
            # so solving for basis and substituting pose = delta @ rest (and
            # likewise for the parent) collapses to
            #     basis = rest^-1 @ parent_delta^-1 @ delta @ rest
            # with the parent terms cancelling out entirely.
            #
            # Deriving basis directly avoids assigning pose_bone.matrix, which
            # is a dependent property: Blender back-solves it from the parent's
            # *currently evaluated* pose, so it needs a correct depsgraph
            # update between every bone and silently produces wrong results if
            # evaluation order or timing is off. This form has no such
            # dependency, and delta == identity provably yields basis ==
            # identity, i.e. exactly zero deformation at the bind pose.
            rest_local = db.matrix_local
            delta = delta_for(bname, frame_id)

            parent_name = entry.get("parent") or ""
            db_parent = db.parent
            if db_parent is not None and parent_name in table:
                parent_delta = delta_for(parent_name, frame_id)
                basis = rest_local.inverted_safe() @ parent_delta.inverted_safe() @ delta @ rest_local
            else:
                basis = rest_local.inverted_safe() @ delta @ rest_local

            loc, rot, scl = basis.decompose()
            # A quaternion and its negation describe the same orientation, but
            # Blender interpolates components linearly -- so if consecutive keys
            # land on opposite signs the bone spins the long way round between
            # them. Keep each bone's quaternion on the same hemisphere as its
            # previous key.
            prev_q = prev_quat.get(bname)
            if prev_q is not None and rot.dot(prev_q) < 0.0:
                rot.negate()
            prev_quat[bname] = rot.copy()

            pb.rotation_mode = 'QUATERNION'
            pb.location = loc
            pb.rotation_quaternion = rot
            pb.scale = scl

            pb.keyframe_insert("location", frame=frame_id, group=bname)
            pb.keyframe_insert("rotation_quaternion", frame=frame_id, group=bname)
            pb.keyframe_insert("scale", frame=frame_id, group=bname)
            keyed_bones.add(bname)
            max_frame = max(max_frame, frame_id)

    animated = len(keyed_bones)

    if fps_values:
        # fps is uniform within a file but ranges from 1 to 240 across the
        # animation library, so pick the most common value rather than the
        # smallest, which would badly misreport playback speed.
        fps = collections.Counter(fps_values).most_common(1)[0][0]
        try:
            bpy.context.scene.render.fps = int(fps)
        except Exception:
            pass
        arm_obj["mp_anim_fps"] = int(fps)
        log.write("  animation fps: %s (scene set to %i)"
                  % (sorted(set(fps_values)), int(fps)))

    if max_frame:
        bpy.context.scene.frame_end = max(bpy.context.scene.frame_end, max_frame)
    log.write("  animation: %i bones keyed, last frame %i" % (animated, max_frame))
    clear_pose_cache()
    return animated, max_frame
