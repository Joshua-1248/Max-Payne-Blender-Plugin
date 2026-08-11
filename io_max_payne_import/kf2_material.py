"""Material and facial-animation-texture support for Max Payne KF2 models.

Max Payne does not use morph targets or bone-driven facial rigs. Facial
expressions are texture swaps: a KF2 Texture chunk holds a *list* of texture
names plus a TextureAnimationInfo describing playback (start frame, fps, and
an end condition of Loop / PingPong / Hold). Sets like

    Face_MaxPayne_Default / _Grin / _Wounded / _Death
    face_male16_default / _idle / _grin / _dead

are the frames of such a list. Any exporter that writes a single diffuse
texture per material silently destroys this, which is why round-tripping a
character through most tools loses its expressions.

This module keeps every frame: all frames are loaded as Blender images, the
first is wired into the shader, and the complete frame list plus playback
settings are preserved as custom properties on the material so an exporter can
reconstruct the chunk exactly.
"""

import os
import traceback

import bpy


# TextureAnimationInfo.end_condition values, per the format notes.
END_CONDITION_NAMES = {0: 'LOOP', 1: 'PINGPONG', 2: 'HOLD'}

# Material.diffuse_texture_type / reflection_texture_type
TEXTURE_BLEND_NAMES = {0: 'COPY', 1: 'ADDITIVE', 2: 'MULTIPLICATIVE'}


def find_texture_file(tex_name, search_index):
    """KF2 stores bare texture names; resolve against the user's texture
    folder index (built the same way the level importer does it)."""
    if not tex_name or not search_index:
        return None
    base = str(tex_name).replace("\\", "/")
    base = os.path.basename(base)
    stem = os.path.splitext(base)[0]
    return search_index.get(base.lower()) or search_index.get(stem.lower())


def load_image_for_texture(tex_name, search_index, img_cache, log):
    if tex_name in img_cache:
        return img_cache[tex_name]

    path = find_texture_file(tex_name, search_index)
    if path is None:
        log.write("    texture '%s' not found in texture folder" % tex_name)
        img_cache[tex_name] = None
        return None

    img = None
    try:
        if path.lower().endswith(".pcx"):
            # Reuse the addon's PCX decoder.
            from . import decode_pcx_bytes
            w, h, pixels = decode_pcx_bytes(open(path, "rb").read())
            img = bpy.data.images.new(os.path.basename(path), width=w, height=h, alpha=True)
            img.pixels = pixels
        else:
            img = bpy.data.images.load(path, check_existing=True)
            if img.size[0] == 0:
                bpy.data.images.remove(img)
                raise ValueError("decoded to 0x0")
        img.pack()
        log.write("    texture '%s' -> %s (%ix%i)" % (tex_name, path, img.size[0], img.size[1]))
    except Exception as e:
        log.write("    texture '%s' FAILED to load from %s: %s" % (tex_name, path, e))
        img = None

    img_cache[tex_name] = img
    return img


def describe_texture(tex):
    """Return (frame_names, anim_info_dict or None)."""
    if tex is None:
        return [], None
    frames = [str(t) for t in (getattr(tex, "textures", None) or []) if t]
    info = getattr(tex, "animation_info", None)
    if info is None:
        return frames, None
    return frames, {
        "is_automatic_start": bool(getattr(info, "is_automatic_start", 0)),
        "is_random_start_frame": bool(getattr(info, "is_random_start_frame", 0)),
        "start_frame": int(getattr(info, "start_frame", 0)),
        "playback_fps": int(getattr(info, "playback_fps", 0)),
        "end_condition": int(getattr(info, "end_condition", 0)),
    }


def build_kf2_material(kf2_material, search_index, img_cache, log):
    """Create a Blender material from a KF2 Material, preserving every
    animated texture frame."""
    name = getattr(kf2_material, "name", None) or "kf2_material"
    existing = bpy.data.materials.get(name)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF") or nodes.new("ShaderNodeBsdfPrincipled")

    if getattr(kf2_material, "is_two_sided", False):
        mat.use_backface_culling = False

    # Base colour from the material's diffuse colour.
    try:
        bsdf.inputs["Base Color"].default_value = (
            float(kf2_material.diffuse_color_r),
            float(kf2_material.diffuse_color_g),
            float(kf2_material.diffuse_color_b),
            1.0,
        )
    except Exception:
        pass

    try:
        spec_exp = float(getattr(kf2_material, "specular_exponent", 0.0) or 0.0)
        if spec_exp > 0:
            # Higher exponent = tighter highlight = lower roughness.
            bsdf.inputs["Roughness"].default_value = max(0.05, min(1.0, 1.0 / (1.0 + spec_exp * 0.1)))
    except Exception:
        pass

    log.write("  material '%s'" % name)

    # --- Diffuse, including facial-animation frame lists -------------------
    diffuse = getattr(kf2_material, "diffuse_texture", None)
    frames, anim = describe_texture(diffuse)

    if frames:
        images = []
        for fn in frames:
            img = load_image_for_texture(fn, search_index, img_cache, log)
            if img is not None:
                images.append((fn, img))

        if images:
            tex_node = nodes.new("ShaderNodeTexImage")
            tex_node.image = images[0][1]
            tex_node.location = (bsdf.location.x - 400, bsdf.location.y + 150)
            tex_node.label = "Diffuse (frame 1 of %i)" % len(images)
            tex_node.name = "MP_DiffuseTexture"
            links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])
            if getattr(kf2_material, "has_opacity_texture", False) or \
               getattr(kf2_material, "has_vertex_alpha", False):
                links.new(tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
                mat.blend_method = 'BLEND'

        # Preserve the full frame list for export, whether or not the
        # images resolved on disk.
        mat["mp_diffuse_frames"] = frames
        if anim is not None:
            mat["mp_texture_animation"] = anim
            mat["mp_texture_end_condition"] = END_CONDITION_NAMES.get(
                anim["end_condition"], str(anim["end_condition"]))
            log.write("    FACIAL/ANIMATED TEXTURE: %i frames, %i fps, end=%s, start_frame=%i"
                      % (len(frames), anim["playback_fps"],
                         mat["mp_texture_end_condition"], anim["start_frame"]))
            log.write("    frames: %s" % ", ".join(frames))

    # --- Secondary texture slots, preserved for round-tripping -------------
    for attr, label in (("reflection_texture", "reflection"),
                        ("bump_texture", "bump"),
                        ("opacity_texture", "opacity"),
                        ("mask_texture", "mask")):
        tex = getattr(kf2_material, attr, None)
        tframes, tanim = describe_texture(tex)
        if not tframes:
            continue
        mat["mp_%s_frames" % label] = tframes
        if tanim is not None:
            mat["mp_%s_animation" % label] = tanim
        img = load_image_for_texture(tframes[0], search_index, img_cache, log)
        if img is not None:
            n = nodes.new("ShaderNodeTexImage")
            n.image = img
            n.label = label
            n.location = (bsdf.location.x - 400, bsdf.location.y - 250 * (1 + len(mat.keys()) % 4))
            if label == "opacity":
                links.new(n.outputs["Color"], bsdf.inputs["Alpha"])
                mat.blend_method = 'BLEND'

    # Preserve raw flags so an exporter can rebuild the chunk faithfully.
    for attr in ("is_two_sided", "is_fogging", "is_diffuse_combined",
                 "is_invisible_geometry", "has_vertex_alpha",
                 "has_diffuse_texture", "has_reflection_texture",
                 "has_bump_texture", "has_opacity_texture", "has_mask_texture",
                 "has_lit", "diffuse_color_type", "specular_color_type",
                 "lit_type", "mask_texture_type", "diffuse_texture_type",
                 "reflection_texture_type", "vertex_alpha", "specular_exponent"):
        try:
            val = getattr(kf2_material, attr)
            if isinstance(val, bool):
                val = int(val)
            if isinstance(val, (int, float, str)):
                mat["mp_%s" % attr] = val
        except Exception:
            pass

    return mat


def build_materials_from_lists(material_lists, search_index, log):
    """Returns {material_name: bpy Material} and an ordered list."""
    img_cache = {}
    by_name = {}
    ordered = []
    for mlist in material_lists:
        tex_dirs = getattr(mlist, "texture_dirs", "")
        if tex_dirs:
            log.write("  material list texture dirs: %s" % tex_dirs)
        for kf2_mat in (getattr(mlist, "materials", None) or []):
            try:
                mat = build_kf2_material(kf2_mat, search_index, img_cache, log)
            except Exception:
                log.write("  material failed:\n" + traceback.format_exc())
                continue
            by_name[getattr(kf2_mat, "name", "")] = mat
            ordered.append(mat)
    return by_name, ordered


def setup_facial_animation_drivers(materials, log):
    """Expose each animated-texture material's current frame as a keyframable
    custom property, so expressions can be previewed and scrubbed in Blender.

    The game switches these frames by state (idle / grin / wounded / dead)
    rather than playing them back linearly, so no timeline keys are inserted
    automatically -- the property is left for the user to drive."""
    count = 0
    for mat in materials:
        frames = mat.get("mp_diffuse_frames")
        if not frames or len(frames) < 2:
            continue
        mat["mp_current_frame"] = 0
        try:
            ui = mat.id_properties_ui("mp_current_frame")
            ui.update(min=0, max=len(frames) - 1,
                      description="Facial/animated texture frame index: " + ", ".join(frames))
        except Exception:
            pass
        count += 1
    if count:
        log.write("  %i material(s) carry animated texture frame sets" % count)
    return count
