"""Blender importer for Max Payne KF2 / KFS / SKD model files.

KFS (mesh data) and SKD (skin data) use the same chunked container as KF2 --
per the official format notes, KFS is the Mesh section and SKD is the Skin
section of that same structure -- so a single reader and a single importer
covers all three extensions.
"""

import os
import traceback

import bpy
import bmesh
from mathutils import Vector, Matrix

from max_payne_sdk.max_kf2 import MaxKF2Reader
import max_payne_sdk.max_kf2_type as kf2_type

from . import kf2_material
from . import kf2_skeleton


class _NoSkeletonData(Exception):
    """Raised when bone transforms are unavailable, so no armature is built."""


# Same axis conversion used for level geometry: game -> Blender is
# (x, y, z) -> (-x, -z, y).
def conv_point(v, scale=1.0):
    return Vector((-v[0], -v[2], v[1])) * scale


def conv_node_transform(rows, scale=1.0):
    """KF2 node transforms are 4x3/4x4 row-vector matrices in game space."""
    if not rows:
        return Matrix.Identity(4)
    try:
        A3 = Matrix(((-1.0, 0.0, 0.0), (0.0, 0.0, -1.0), (0.0, 1.0, 0.0)))
        L = Matrix((
            (rows[0][0], rows[0][1], rows[0][2]),
            (rows[1][0], rows[1][1], rows[1][2]),
            (rows[2][0], rows[2][1], rows[2][2]),
        ))
        t = Vector((rows[3][0], rows[3][1], rows[3][2]))
        linear_b = A3 @ L.transposed() @ A3.transposed()
        m = linear_b.to_4x4()
        m.translation = (A3 @ t) * scale
        return m
    except Exception:
        return Matrix.Identity(4)


def collect_uv_layers(mesh_chunk):
    """Return list of (layer_index, coordinates, polygons_uv_indices)."""
    layers = []
    for uvm in (mesh_chunk.uv_mapping or []):
        coords = getattr(uvm, "coordinates", None) or []
        indices = getattr(uvm, "polygons_uv_indices", None) or []
        layers.append((getattr(uvm, "layer", len(layers)), coords, indices))
    return layers


def build_mesh_from_kf2(mesh_chunk, scale, log, index):
    geometry = mesh_chunk.geometry
    polygons = mesh_chunk.polygons
    if geometry is None or polygons is None:
        log.write("  mesh %i: no geometry/polygons chunk, skipping" % index)
        return None

    verts_src = geometry.vertices or []
    indices = polygons.polygons_indices or []
    if not verts_src or not indices:
        log.write("  mesh %i: empty geometry, skipping" % index)
        return None

    node = mesh_chunk.node
    name = (getattr(node, "name", None) or "KF2_Mesh_%03d" % index)

    bm = bmesh.new()
    bm_verts = []
    for v in verts_src:
        try:
            bm_verts.append(bm.verts.new(conv_point(v, scale)))
        except Exception:
            bm_verts.append(bm.verts.new(Vector((0.0, 0.0, 0.0))))
    bm.verts.ensure_lookup_table()

    uv_layers_src = collect_uv_layers(mesh_chunk)
    bm_uv_layers = []
    for i, (layer_idx, coords, uv_indices) in enumerate(uv_layers_src):
        lname = "UVMap" if i == 0 else "UVMap.%03d" % i
        bm_uv_layers.append((bm.loops.layers.uv.new(lname), coords, uv_indices))

    # Per-polygon material assignment, when present.
    mat_for_poly = []
    if mesh_chunk.polygon_material is not None:
        mat_for_poly = getattr(mesh_chunk.polygon_material,
                               "material_index_for_polygon", []) or []

    faces = []
    tri_count = len(indices) // 3
    for tri in range(tri_count):
        a, b, c = indices[tri * 3], indices[tri * 3 + 1], indices[tri * 3 + 2]
        if max(a, b, c) >= len(bm_verts):
            faces.append(None)
            continue
        try:
            # Reverse winding to match the level-geometry convention.
            face = bm.faces.new((bm_verts[a], bm_verts[c], bm_verts[b]))
        except ValueError:
            faces.append(None)
            continue

        for uv_layer, coords, uv_indices in bm_uv_layers:
            if tri < len(uv_indices):
                pui = uv_indices[tri]
                vi = getattr(pui, "uv_index", None) or getattr(pui, "uv_indices", None) or []
            else:
                vi = [a, b, c]
            # Face was built as (a, c, b), so match that ordering.
            order = [0, 2, 1] if len(vi) >= 3 else list(range(len(vi)))
            for loop, oi in zip(face.loops, order):
                if oi < len(vi) and vi[oi] < len(coords):
                    uv = coords[vi[oi]]
                    # KF2 stores V running negative (0 at the top, decreasing
                    # downward), so negating maps it into Blender's 0..1 space.
                    # Using 1-v here would offset every texture by a full tile.
                    loop[uv_layer].uv = (uv[0], -uv[1])

        faces.append(face)

    # material_index_for_polygon holds direct indices into the mesh's
    # POLYGON_MATERIAL name list, so they are used as-is; the material slots
    # are filled in that same order by the caller.
    for tri, face in enumerate(faces):
        if face is None:
            continue
        if tri < len(mat_for_poly):
            mid = mat_for_poly[tri]
            if isinstance(mid, list):
                mid = mid[0] if mid else 0
            try:
                face.material_index = max(0, int(mid))
            except (TypeError, ValueError):
                pass

    bm.normal_update()
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()

    # Author-supplied normals, when the file has them.
    normals = getattr(geometry, "normals", None) or []
    if len(normals) == len(me.vertices):
        try:
            me.use_auto_smooth = True
            me.normals_split_custom_set_from_vertices(
                [conv_point(n, 1.0).normalized() for n in normals])
        except Exception:
            pass

    obj = bpy.data.objects.new(name, me)
    obj.matrix_world = conv_node_transform(
        getattr(node, "object_to_parent_transform", None), scale)

    if node is not None:
        obj["kf2_node_name"] = getattr(node, "name", "") or ""
        obj["kf2_parent_name"] = getattr(node, "parent_name", "") or ""
        obj["kf2_user_string"] = getattr(node, "user_defined_string", "") or ""

    log.write("  mesh '%s': %i verts, %i tris, %i uv layers"
              % (name, len(me.vertices), len(me.polygons), len(bm_uv_layers)))
    return obj


def apply_skins(kf2, objects_by_name, log):
    """Create vertex groups from SKIN chunks so bone weights survive import."""
    created = 0
    for skin in kf2.getSkins():
        skin_names = getattr(skin, "skin_object_names", []) or []
        bone_names = getattr(skin, "skeleton_object_names", []) or []
        verts = getattr(skin, "skin_vertices", []) or []
        log.write("  skin: %i target objects, %i bones, %i weighted vertices"
                  % (len(skin_names), len(bone_names), len(verts)))

        targets = [objects_by_name[n] for n in skin_names if n in objects_by_name]
        if not targets:
            log.write("    (no matching mesh objects in this file for those names)")
            continue

        for obj in targets:
            groups = {}
            for bi, bname in enumerate(bone_names):
                groups[bi] = obj.vertex_groups.get(bname) or obj.vertex_groups.new(name=bname)

            nverts = len(obj.data.vertices)
            for sv in verts:
                vidx = getattr(sv, "vertex_index", None)
                if vidx is None or vidx >= nverts:
                    continue
                bidx = getattr(sv, "vertex_bone_indices", []) or []
                wts = getattr(sv, "vertex_weights", []) or []
                for bi, w in zip(bidx, wts):
                    grp = groups.get(bi)
                    if grp is not None and w:
                        grp.add([vidx], float(w), 'REPLACE')
            created += 1
    return created


# Files that supply the neutral bind pose rather than motion. The standard
# human rig ships as Widepose.kf2 in skeletons/default_skeleton/anim. Note the
# library also contains single-frame *death* poses (dead_lisa, Dead_Body2);
# those are poses but not bind poses, so they are deliberately not matched.
POSE_FILE_HINTS = ("widepose", "basepose", "defaultpose", "restpose", "bindpose")


def looks_like_pose_file(path):
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if any(h in stem for h in POSE_FILE_HINTS):
        return True
    # A bare "pose" name (Pose.kf2, ColtCommando_pose.kf2) is a bind pose,
    # but avoid matching names that merely contain the word incidentally.
    return stem == "pose" or stem.endswith("_pose")


def auto_rig_scene(log, scale=1.0):
    """Make every Max Payne mesh in the scene ready to animate.

    Called at the end of every import so the pieces connect themselves no
    matter what order they arrive in -- mesh first, skin first, or pose first.
    A mesh only deforms if it has BOTH vertex groups and an Armature modifier
    pointing at the rig; this pairs them up wherever both exist.

    Idempotent: running it repeatedly changes nothing once things are bound.
    """
    armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']
    if not armatures:
        return 0

    # Prefer a rig built from a proper bind pose.
    armatures.sort(key=lambda a: 0 if a.get("mp_rest_scale") is not None else 1)

    bound = 0
    for arm in armatures:
        bone_names = {b.name for b in arm.data.bones}
        if not bone_names:
            continue
        for obj in bpy.data.objects:
            if obj.type != 'MESH' or not obj.vertex_groups:
                continue
            groups = {g.name for g in obj.vertex_groups}
            overlap = groups & bone_names
            # Require a real match so unrelated meshes are never touched.
            if len(overlap) < max(2, len(groups) * 0.5):
                continue

            mod = None
            for m in obj.modifiers:
                if m.type == 'ARMATURE':
                    mod = m
                    break
            if mod is None:
                mod = obj.modifiers.new(name="Armature", type='ARMATURE')
                log.write("  auto-rig: added Armature modifier to '%s'" % obj.name)
            if mod.object is not arm:
                mod.object = arm
                log.write("  auto-rig: bound '%s' -> '%s' (%i/%i groups match bones)"
                          % (obj.name, arm.name, len(overlap), len(groups)))
                bound += 1
            mod.use_vertex_groups = True

            if obj.parent is None:
                try:
                    world = obj.matrix_world.copy()
                    obj.parent = arm
                    obj.matrix_parent_inverse = arm.matrix_world.inverted_safe()
                    obj.matrix_world = world
                except Exception:
                    pass
        if bound:
            break  # one rig is enough
    return bound


def import_character(filepaths, scale, log, collection, search_index=None,
                     import_materials=True, import_skeleton=True,
                     rest_pose_path=""):
    """Import a character from one or more KF2/KFS/SKD files.

    A Max Payne character is split across files: the mesh and materials live
    in a .KFS, the skin weights and bone-name list in a .SKD, and the bone
    hierarchy plus transforms in a .KF2 animation. They are merged here so
    selecting all of them together produces one rigged, animated character."""
    meshes = []
    skins = []
    material_lists = []
    animations = []
    rest_animations = []

    all_paths = list(filepaths)
    explicit_rest = ""
    if rest_pose_path:
        explicit_rest = bpy.path.abspath(rest_pose_path)
        if explicit_rest not in all_paths:
            all_paths.append(explicit_rest)

    for path in all_paths:
        try:
            kf2 = MaxKF2Reader().parse(path)
        except Exception:
            log.write("FAILED to parse %s:\n%s" % (path, traceback.format_exc()))
            continue
        log.write("File %s: meshes=%i skins=%i matlists=%i anims=%i"
                  % (os.path.basename(path), kf2.numMeshes(), kf2.numSkins(),
                     kf2.numMaterialList(), kf2.numKeyframeAnimations()))
        meshes.extend(kf2.getMeshes())
        skins.extend(kf2.getSkins())
        material_lists.extend(kf2.getMaterialList())

        anims = kf2.getKeyframeAnimations()
        is_rest = (explicit_rest and os.path.normcase(path) == os.path.normcase(explicit_rest)) \
            or (not explicit_rest and looks_like_pose_file(path))
        if anims and is_rest:
            rest_animations.extend(anims)
            log.write("  -> treated as REST POSE (bind pose for the skeleton)")
        else:
            animations.extend(anims)

    log.write("-" * 60)
    log.write("Merged: %i meshes, %i skins, %i material lists, %i animations, "
              "%i rest-pose bones"
              % (len(meshes), len(skins), len(material_lists), len(animations),
                 len(rest_animations)))

    # --- Materials -----------------------------------------------------
    materials_by_name = {}
    ordered_materials = []
    if import_materials and material_lists:
        try:
            materials_by_name, ordered_materials = kf2_material.build_materials_from_lists(
                material_lists, search_index or {}, log)
            kf2_material.setup_facial_animation_drivers(ordered_materials, log)
        except Exception:
            log.write("Material import failed:\n" + traceback.format_exc())

    # --- Meshes --------------------------------------------------------
    objects = []
    objects_by_name = {}
    mesh_nodes = {}
    for i, mesh_chunk in enumerate(meshes):
        try:
            obj = build_mesh_from_kf2(mesh_chunk, scale, log, i)
        except Exception:
            log.write("  mesh %i failed:\n%s" % (i, traceback.format_exc()))
            continue
        if obj is None:
            continue

        node = getattr(mesh_chunk, "node", None)
        if node is not None and getattr(node, "name", None):
            mesh_nodes[node.name] = (getattr(node, "parent_name", "") or "",
                                     getattr(node, "object_to_parent_transform", None))

        if materials_by_name:
            try:
                pm = mesh_chunk.polygon_material
                names = list(getattr(pm, "name", None) or []) if pm else []
                if isinstance(names, str):
                    names = [names]
                # Slot order must match material_index_for_polygon exactly.
                for mname in names:
                    mat = materials_by_name.get(mname)
                    if mat is None:
                        mat = bpy.data.materials.get(mname) or bpy.data.materials.new(mname)
                        log.write("  material '%s' referenced by mesh but not in any "
                                  "material list; placeholder created" % mname)
                    obj.data.materials.append(mat)
            except Exception:
                log.write("  material assignment failed:\n" + traceback.format_exc())

        collection.objects.link(obj)
        objects.append(obj)
        node_name = getattr(node, "name", None)
        if node_name:
            objects_by_name[node_name] = obj

    # --- Skeleton, weights, animation ----------------------------------
    arm_obj = None
    animated = 0

    # An .SKD on its own carries only weights -- no mesh and no bone positions.
    # Rather than fail, adopt a mesh and armature already in the scene, which
    # supports importing the pieces one at a time instead of all together.
    adopted_mesh = False
    if skins and not meshes:
        for skin in skins:
            for sname in (getattr(skin, "skin_object_names", None) or []):
                existing = bpy.data.objects.get(sname)
                if existing is not None and existing.type == 'MESH':
                    objects_by_name[sname] = existing
                    adopted_mesh = True
                    log.write("Found '%s' already in the scene; applying skin "
                              "weights to it" % sname)
                else:
                    log.write("Skin targets '%s' but no such mesh is in the scene. "
                              "Import the .KFS first (or select it alongside the .SKD)."
                              % sname)

    existing_arm = None
    if not rest_animations and not animations:
        # No bone positions in this import; reuse an armature already built
        # from a pose file.
        for cand in bpy.data.objects:
            if cand.type == 'ARMATURE' and cand.get("mp_rest_scale") is not None:
                existing_arm = cand
                break
        if existing_arm is None:
            for cand in bpy.data.objects:
                if cand.type == 'ARMATURE':
                    existing_arm = cand
                    break

    if skins and existing_arm is not None and not (animations or rest_animations):
        log.write("Using existing armature '%s' in the scene" % existing_arm.name)
        arm_obj = existing_arm
        rig_bones = {b.name for b in existing_arm.data.bones}
        for skin in skins:
            bone_names = list(getattr(skin, "skeleton_object_names", None) or [])
            missing = [b for b in bone_names if b not in rig_bones]
            if missing:
                log.write("  %i bone(s) named by the skin are absent from that "
                          "armature: %s" % (len(missing), ", ".join(missing[:8])))
            for sname in (getattr(skin, "skin_object_names", None) or []):
                target = objects_by_name.get(sname) or bpy.data.objects.get(sname)
                if target is None or target.type != 'MESH':
                    log.write("  no mesh '%s' to weight" % sname)
                    continue
                kf2_skeleton.apply_skin_weights(target, skin, bone_names, log)
                kf2_skeleton.bind_mesh_to_armature(target, existing_arm)
                # Confirm the bind actually took: without an Armature modifier
                # the weights exist but the mesh never follows the bones.
                mods = [m for m in target.modifiers if m.type == 'ARMATURE']
                if not mods:
                    log.write("  WARNING: could not add an Armature modifier to '%s'"
                              % target.name)
                elif mods[0].object is not existing_arm:
                    mods[0].object = existing_arm
                    log.write("  repointed the existing Armature modifier on '%s' to '%s'"
                              % (target.name, existing_arm.name))
                else:
                    log.write("  bound '%s' to '%s' (Armature modifier active)"
                              % (target.name, existing_arm.name))
                if target.name not in [o.name for o in collection.objects]:
                    objects.append(target)
        auto_rig_scene(log, scale)
    return objects, len(skins), arm_obj, animated

    if import_skeleton and (skins or animations or rest_animations):
        try:
            table = kf2_skeleton.build_bone_table(
                animations, mesh_nodes, rest_animations)

            if rest_animations:
                log.write("  rest pose supplied by a dedicated pose file")
            elif animations:
                log.write("  NOTE: no rest/bind pose file supplied. Using the first "
                          "frame of an animation as the rest pose, which is a posed "
                          "frame rather than the neutral bind pose and can skew the "
                          "rig. Add the skeleton's pose file (Widepose.kf2 for the "
                          "standard human rig, in "
                          "Data/database/skeletons/default_skeleton/anim).")

            bone_names = []
            for skin in skins:
                for b in (getattr(skin, "skeleton_object_names", None) or []):
                    if b not in bone_names:
                        bone_names.append(b)
            if not bone_names:
                # Animation-only file: every animated object is a bone.
                bone_names = list(table.keys())

            if bone_names:
                # Bone placement comes only from animation chunks. Without an
                # animation file every bone would collapse to the origin, so
                # refuse to build a skeleton that is certainly wrong.
                with_transforms = [b for b in bone_names
                                   if table.get(b, {}).get("rest_rows") is not None]
                if not with_transforms:
                    log.write("")
                    log.write("*" * 62)
                    log.write("NO SKELETON AVAILABLE")
                    log.write("Bone POSITIONS are not stored in the .KFS or the .SKD.")
                    log.write("They live only in a pose/animation .KF2:")
                    log.write("    .KFS   mesh + materials")
                    log.write("    .SKD   skin weights + bone names (no positions)")
                    log.write("    .KF2   bone hierarchy + positions")
                    log.write("")
                    log.write("FIX: import the bind pose first, then re-import this file:")
                    log.write("    Data/database/skeletons/default_skeleton/anim/Widepose.kf2")
                    log.write("    (rat rig: rat_stand_pose.kf2)")
                    log.write("Importing Widepose.kf2 on its own builds the armature; this")
                    log.write("file will then find it automatically and bind to it.")
                    log.write("Selecting all the files together in one import also works.")
                    log.write("*" * 62)
                    raise _NoSkeletonData()

                partial = len(bone_names) - len(with_transforms)
                if partial:
                    log.write("  NOTE: %i of %i bones lack transforms and will sit at "
                              "the origin" % (partial, len(bone_names)))

                arm_name = "MP_Armature"
                arm_obj = kf2_skeleton.build_armature(
                    bone_names, table, scale, collection, arm_name, log)

                for skin in skins:
                    for sname in (getattr(skin, "skin_object_names", None) or []):
                        target = objects_by_name.get(sname)
                        if target is None:
                            log.write("  skin targets '%s' but no such mesh was loaded "
                                      "(load the matching .KFS alongside the .SKD)" % sname)
                            continue
                        kf2_skeleton.apply_skin_weights(target, skin, bone_names, log)
                        kf2_skeleton.bind_mesh_to_armature(target, arm_obj)

                if animations:
                    animated, _ = kf2_skeleton.apply_animation(
                        arm_obj, bone_names, table, scale, "MP_Action", log)
        except _NoSkeletonData:
            arm_obj = None
        except Exception:
            log.write("Skeleton setup failed:\n" + traceback.format_exc())

    auto_rig_scene(log, scale)
    return objects, len(skins), arm_obj, animated
