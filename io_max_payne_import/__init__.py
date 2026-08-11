bl_info = {
    "name": "Max Payne LDB Importer",
    "author": "Ported to Blender from m0nstr0/max_payne_ldb_importer",
    "version": (1, 0, 0),
    "blender": (3, 3, 0),
    "location": "File > Import > Max Payne Level (.ldb)",
    "description": "Import Max Payne 1 / Max Payne 2 .ldb level files (rooms, static geometry, dynamic meshes, materials)",
    "category": "Import-Export",
}

import os
import sys
import tempfile
import traceback

import bpy
import bmesh
from bpy_extras.io_utils import ImportHelper
from bpy.props import StringProperty, BoolProperty, FloatProperty
from mathutils import Matrix, Vector

# Make the bundled, unmodified max_payne_sdk importable regardless of how
# Blender loaded this addon package.
_ADDON_DIR = os.path.dirname(os.path.abspath(__file__))
if _ADDON_DIR not in sys.path:
    sys.path.append(_ADDON_DIR)

from max_payne_sdk.max_ldb_factory import MaxLBDReaderFactory
from max_payne_sdk.ldb.max_ldb_type import MaxLDB
from max_payne_sdk.ldb2.max_ldb2_type import MaxLDB2


# ---------------------------------------------------------------------------
# Coordinate conversion
#
# The original Maya plugin builds geometry directly in Maya's convention:
# Maya is right-handed, Y-up. It negates the game's X axis to fix handedness
# and leaves Y/Z as-is (Maya Y-up == game Y-up).
#
# Blender is right-handed, Z-up. Converting Maya-convention -> Blender-
# convention is the well-known swap: blender = (maya_x, -maya_z, maya_y).
# Composing both steps: blender = (-game_x, -game_z, game_y).
#
# A3 below is that composed linear map, expressed so that A3 @ v performs
# the conversion on a column vector.
# ---------------------------------------------------------------------------

A3 = Matrix((
    (-1.0, 0.0, 0.0),
    (0.0, 0.0, -1.0),
    (0.0, 1.0, 0.0),
))
A3_T = A3.transposed()  # A3 is orthogonal, so A3.inverted() == A3.transposed()


def conv_point(v, scale=1.0):
    return Vector((-v.x, -v.z, v.y)) * scale


def conv_vector(v):
    r = Vector((-v.x, -v.z, v.y))
    if r.length_squared > 0.0:
        r.normalize()
    return r


def conv_transform(rows, scale=1.0):
    """rows: 4x4 game-space transform as 4 lists (row-vector convention,
    i.e. world = local_row @ M; rows[3] is translation). Returns a Blender
    mathutils.Matrix (column-vector convention, world = M @ local)."""
    if rows is None:
        return Matrix.Identity(4)
    L = Matrix((
        (rows[0][0], rows[0][1], rows[0][2]),
        (rows[1][0], rows[1][1], rows[1][2]),
        (rows[2][0], rows[2][1], rows[2][2]),
    ))
    t = Vector((rows[3][0], rows[3][1], rows[3][2]))
    linear_b = A3 @ L.transposed() @ A3_T
    t_b = (A3 @ t) * scale
    m = linear_b.to_4x4()
    m.translation = t_b
    return m


def compose_game_transform(parent_rows, child_rows):
    """Re-implementation of max_payne_maya.ldb.max_math.transformNodeWithParent,
    kept in the original game-space row-vector convention so it can be
    composed before a single axis conversion at the end."""
    if parent_rows is None:
        return child_rows
    if child_rows is None:
        return parent_rows

    def inv_rotation_matrix(in_m):
        m = [row[:] for row in in_m]
        det = (m[0][0] * (m[1][1] * m[2][2] - m[2][1] * m[1][2])
               - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
               + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0]))
        if det == 0:
            return m
        invdet = 1.0 / det
        m[0][0] = (m[1][1] * m[2][2] - m[2][1] * m[1][2]) * invdet
        m[0][1] = (m[0][2] * m[2][1] - m[0][1] * m[2][2]) * invdet
        m[0][2] = (m[0][1] * m[1][2] - m[0][2] * m[1][1]) * invdet
        m[1][0] = (m[1][2] * m[2][0] - m[1][0] * m[2][2]) * invdet
        m[1][1] = (m[0][0] * m[2][2] - m[0][2] * m[2][0]) * invdet
        m[1][2] = (m[1][0] * m[0][2] - m[0][0] * m[1][2]) * invdet
        m[2][0] = (m[1][0] * m[2][1] - m[2][0] * m[1][1]) * invdet
        m[2][1] = (m[2][0] * m[0][1] - m[0][0] * m[2][1]) * invdet
        m[2][2] = (m[0][0] * m[1][1] - m[1][0] * m[0][1]) * invdet
        return m

    result = [row[:] for row in parent_rows]
    result = inv_rotation_matrix(result)
    for i in range(3):
        for j in range(3):
            s = 0.0
            for k in range(3):
                s += parent_rows[i][k] * child_rows[k][j]
            result[i][j] = s
    result[3][0] = parent_rows[3][0] + child_rows[3][0]
    result[3][1] = parent_rows[3][1] + child_rows[3][1]
    result[3][2] = parent_rows[3][2] + child_rows[3][2]
    return result


# ---------------------------------------------------------------------------
# Plain-python proxy layer (re-implementation of max_payne_maya/ldb/ldb_proxy.py
# without any Maya dependency; returns mathutils types instead of OpenMaya
# types, and raw game-space transform rows instead of baking them into Maya
# group nodes).
# ---------------------------------------------------------------------------

class TextureInfo:
    __slots__ = ("file_path", "file_type_name", "data")

    def __init__(self, file_path, file_type_name, data):
        self.file_path = file_path
        self.file_type_name = file_type_name
        self.data = data


class MaterialInfo:
    __slots__ = ("id", "name", "diffuse", "alpha", "reflection", "gloss",
                 "detail", "use_alpha")

    def __init__(self, id_, name):
        self.id = id_
        self.name = name
        self.diffuse = None
        self.alpha = None
        self.reflection = None
        self.gloss = None
        self.detail = None
        self.use_alpha = False


class MeshInfo:
    __slots__ = ("vertices", "normals", "us", "vs", "lm_us", "lm_vs",
                 "indices", "vertices_per_poly", "materials", "transform",
                 "use_room_transform")

    def __init__(self):
        self.vertices = []
        self.normals = []
        self.us = []
        self.vs = []
        self.lm_us = []
        self.lm_vs = []
        self.indices = []
        self.vertices_per_poly = []
        self.materials = {}
        self.transform = None
        self.use_room_transform = True


ADDON_BUILD = "2026-08-10o.keyframe-interpolation"


def sniff_texture_format(data, declared=None):
    """Identify an embedded texture from its own bytes.

    The reference SDK's numeric type table has a hole at type 1 (GIF) and
    *raises* on anything it doesn't know, which aborts a whole level import.
    Worse, a stale copy of that module cached in sys.modules keeps raising even
    after the addon is updated, because re-enabling an addon does not reload
    already-imported submodules -- only a full Blender restart does.

    Sniffing the payload sidesteps both problems, so the importer no longer
    depends on the SDK's table being correct or freshly loaded.
    """
    if not data:
        return declared or "unknown"
    if data[:3] == b'\xff\xd8\xff':
        return "jpg"
    if data[:3] == b'GIF':
        return "gif"
    if data[:8] == b'\x89PNG\r\n\x1a\n':
        return "png"
    if data[:4] == b'DDS ':
        return "dds"
    if data[:2] == b'BM':
        return "bmp"
    if data[:1] == b'\x0a' and len(data) > 3 and data[2:3] in (b'\x00', b'\x01'):
        return "pcx"
    if len(data) >= 18:
        if data[-18:] == b'TRUEVISION-XFILE.\x00':
            return "tga"
        # TGA has no leading magic; identify by plausible header fields.
        if data[1] in (0, 1) and data[2] in (1, 2, 3, 9, 10, 11):
            return "tga"
    return declared or "unknown"


def _tex(tex):
    if tex is None:
        return None
    declared = None
    try:
        declared = tex.getFileTypeName()
    except Exception:
        # Old SDK copies raise on unknown types; the payload still tells us.
        declared = None
    if declared and declared.startswith("unknown_type_"):
        declared = None
    data = getattr(tex, "data", None)
    return TextureInfo(tex.file_path, sniff_texture_format(data, declared), data)


def get_materials(ldb):
    result = []
    for i in range(len(ldb.getMaterials())):
        material = ldb.getMaterials()[i]
        info = MaterialInfo(material.id, "max_payne_mat_%i" % material.id)

        if isinstance(ldb, MaxLDB):
            info.diffuse = _tex(material.diffuse_texture)
            info.alpha = _tex(material.alpha_texture)

        elif isinstance(ldb, MaxLDB2):
            info.diffuse = _tex(material.diffuse_texture)
            info.reflection = _tex(material.reflection_texture)
            info.gloss = _tex(material.gloss_texture)
            info.detail = _tex(material.detail_texture)
            if material.properties and material.properties.blend_mode in (4, 1, 2, 10, 11, 8, 7):
                info.use_alpha = True

        result.append(info)
    return result


def mesh_from_ldb1(mesh, transform):
    """mesh: max_payne_sdk.ldb.static_mesh_type.StaticMesh or dynamic_mesh_type.DynamicMesh"""
    info = MeshInfo()
    info.transform = transform
    for polygon_idx in range(mesh.numPolygons()):
        polygon = mesh.polygons[polygon_idx]
        poly_indices = []
        start_vertex = len(info.vertices)
        for i in range(polygon.num_vertices):
            texture_vertex = mesh.texture_vertices[polygon.texture_vertex_idx + i]
            vertex = mesh.vertices[texture_vertex.vertex_idx]
            normal = mesh.normals[texture_vertex.vertex_idx]
            info.vertices.append(Vector((vertex.x, vertex.y, vertex.z)))
            info.normals.append(Vector((normal.x, normal.y, normal.z)))
            info.us.append(texture_vertex.uv.u)
            info.vs.append(1 - texture_vertex.uv.v)
            info.lm_us.append(texture_vertex.lightmap_uv.u)
            info.lm_vs.append(1 - texture_vertex.lightmap_uv.v)
            poly_indices.append(start_vertex + i)
        lm_id = getattr(polygon.lightmap, "id", None) if polygon.lightmap else None
        info.materials.setdefault((polygon.material.id, lm_id), []).append(polygon_idx)
        # Same winding fix as the Maya path: reverse everything but the first vertex.
        poly_indices[1:] = poly_indices[len(poly_indices):0:-1]
        info.indices += poly_indices
        info.vertices_per_poly.append(len(poly_indices))
    return info


def mesh_from_ldb2(mesh_parts, transform):
    """mesh_parts: max_payne_sdk.ldb2.static_mesh_type.StaticMeshContainer (list of parts)"""
    info = MeshInfo()
    info.transform = transform
    start_poly = 0
    for part_id in range(len(mesh_parts)):
        part = mesh_parts[part_id]
        start_vertex = len(info.vertices)
        for index in range(0, len(part.indices), 3):
            info.indices += [
                start_vertex + part.indices[index],
                start_vertex + part.indices[index + 2],
                start_vertex + part.indices[index + 1],
            ]
        info.vertices += [Vector((v.x, v.y, v.z)) for v in part.vertices]
        info.normals += [Vector((n.x, n.y, n.z)) for n in part.normals]
        info.us += [uv.u for uv in part.uvs]
        info.vs += [1 - uv.v for uv in part.uvs]
        info.lm_us += [uv.u for uv in part.lightmap_uvs]
        info.lm_vs += [1 - uv.v for uv in part.lightmap_uvs]
        tri_count = len(part.indices) // 3
        info.materials.setdefault((part.material_id, None), []).extend(
            range(start_poly, start_poly + tri_count))
        start_poly += tri_count
        info.vertices_per_poly += [3] * tri_count
    return info


def get_room_static_mesh(ldb, room_id):
    if isinstance(ldb, MaxLDB2):
        room = ldb.getRooms()[room_id]
        return mesh_from_ldb2(room.static_mesh, room.transform)
    room_static_mesh_id = ldb.getRooms()[room_id].static_meshes[0]
    mesh = ldb.getStaticMeshes().getById(room_static_mesh_id)
    return mesh_from_ldb1(mesh, mesh.transform)


def get_dynamic_meshes_by_room(ldb, room_id):
    """Yields MeshInfo objects for every dynamic mesh belonging to room_id.
    Works for both LDB1 (per-room dynamic mesh name list) and LDB2
    (FSM-linked dynamic meshes) -- the original Maya plugin has working code
    for both but disables LDB2 dynamic meshes via a stray `return 0` in its
    count function. That's re-enabled here."""
    if isinstance(ldb, MaxLDB2):
        for dynamic_mesh in ldb.getDynamicMeshes().dynamic_meshes:
            fsm = ldb.getFSMS()[dynamic_mesh.fsm_id]
            if fsm.room_id != room_id:
                continue
            pivot = dynamic_mesh.aabb.pivot_point
            transform = [row[:] for row in fsm.transform]
            transform[3][0] += pivot[0]
            transform[3][1] += pivot[1]
            transform[3][2] += pivot[2]
            info = mesh_from_ldb2(dynamic_mesh.mesh, transform)
            info.use_room_transform = False
            yield info
    else:
        room = ldb.getRooms()[room_id]
        for dynamic_mesh_name in room.dynamic_meshes:
            mesh = ldb.getDynamicMeshes().getBySharedName(dynamic_mesh_name)
            info = mesh_from_ldb1(mesh, mesh.properties.object_to_room_transform)
            info.use_room_transform = True
            yield info


# ---------------------------------------------------------------------------
# Blender scene building
# ---------------------------------------------------------------------------

class LightInfo:
    __slots__ = ("name", "color", "intensity", "falloff", "transform",
                 "room_id", "use_room_transform")

    def __init__(self):
        self.name = "Light"
        self.color = (1.0, 1.0, 1.0)
        self.intensity = 1.0
        self.falloff = 1.0
        self.transform = None
        self.room_id = 0
        self.use_room_transform = True


def _norm_color(r, g, b):
    """Light colors are stored 0-255 in LDB1 and 0-1 in LDB2. Detect and
    normalize to Blender's 0-1 linear range."""
    vals = [float(r), float(g), float(b)]
    if max(vals) > 1.0:
        vals = [v / 255.0 for v in vals]
    return tuple(max(0.0, min(1.0, v)) for v in vals)


def get_lights(ldb, log):
    """Collect all light sources from either LDB version."""
    lights = []

    if isinstance(ldb, MaxLDB):
        # Point lights: room-relative transform via entity properties.
        try:
            for pl in ldb.getPointlights().pointlights:
                info = LightInfo()
                op = pl.object_properties
                info.name = (op.name or "PointLight_%s" % pl.id)
                info.color = _norm_color(pl.r, pl.g, pl.b)
                info.intensity = float(pl.intensity)
                info.falloff = float(pl.falloff)
                info.transform = op.object_to_room_transform
                info.room_id = op.room_id
                info.use_room_transform = True
                lights.append(info)
        except Exception:
            log.write("Failed reading point lights:\n" + traceback.format_exc())

        # "Dynamic lights" in LDB1 carry their own transform plus ten
        # unlabeled floats; the first three consistently look like RGB.
        try:
            for dl in ldb.getDynamicLights().lights:
                info = LightInfo()
                op = dl.object_properties
                info.name = (dl.shared_name or op.name or "DynamicLight")
                info.color = _norm_color(dl.unk1, dl.unk2, dl.unk3)
                info.intensity = float(dl.unk5) if dl.unk5 else 1.0
                info.falloff = float(dl.unk4) if dl.unk4 else 10.0
                info.transform = dl.transform
                info.room_id = op.room_id
                info.use_room_transform = True
                lights.append(info)
        except Exception:
            log.write("Failed reading dynamic lights:\n" + traceback.format_exc())

    elif isinstance(ldb, MaxLDB2):
        try:
            for dl in ldb.getDynamicLights().dynamic_lights:
                info = LightInfo()
                info.name = "DynamicLight"
                info.color = _norm_color(dl.color.R, dl.color.G, dl.color.B)
                info.intensity = 1.0
                info.falloff = float(dl.Falloff) if dl.Falloff else 10.0
                info.transform = dl.transform
                info.room_id = dl.room_id
                # LDB2 light transforms are already world-space.
                info.use_room_transform = False
                lights.append(info)
        except Exception:
            log.write("Failed reading dynamic lights:\n" + traceback.format_exc())

    return lights


def build_light_object(light_info, world_rows, scale, energy_mult, log):
    """Create a Blender point lamp from a LightInfo.

    Game lights use an intensity multiplier plus a falloff radius. Blender's
    point lamp is radiometric (watts), and brightness falls off with the
    inverse square of distance, so to make a lamp reach roughly as far as the
    game's falloff radius the wattage has to scale with radius squared."""
    lamp_data = bpy.data.lights.new(name=light_info.name, type='POINT')
    lamp_data.color = light_info.color

    radius = max(light_info.falloff, 0.001) * scale
    lamp_data.energy = light_info.intensity * (radius ** 2) * energy_mult
    # Soften the point source a little; a true zero-radius lamp renders
    # very harsh speculars on flat level geometry.
    lamp_data.shadow_soft_size = min(radius * 0.05, 0.5)
    lamp_data.use_custom_distance = True
    lamp_data.cutoff_distance = radius

    obj = bpy.data.objects.new(light_info.name, lamp_data)
    obj.matrix_world = conv_transform(world_rows, scale)
    obj["mp_falloff"] = light_info.falloff
    obj["mp_intensity"] = light_info.intensity
    return obj


def load_lightmap_image(ldb, lm_id, tmp_dir, lm_cache, log):
    """Lightmaps are stored as their own texture records, referenced per
    polygon by id. Returns a bpy Image or None."""
    if lm_id is None:
        return None
    if lm_id in lm_cache:
        return lm_cache[lm_id]

    tex = None
    try:
        lightmaps = ldb.getLightMaps()
        if hasattr(lightmaps, "getTextureById"):
            tex = lightmaps.getTextureById(lm_id)
        elif hasattr(lightmaps, "findLightMapById"):
            tex = lightmaps.findLightMapById(lm_id)
    except Exception:
        log.write("  lightmap %s lookup failed:\n%s" % (lm_id, traceback.format_exc()))

    if tex is None or not getattr(tex, "data", None):
        lm_cache[lm_id] = None
        return None

    try:
        declared = tex.getFileTypeName().lower()
    except Exception:
        declared = None
    if declared and declared.startswith("unknown_type_"):
        declared = None
    ext = sniff_texture_format(getattr(tex, "data", None), declared)

    img = None
    try:
        if ext == "gif":
            from .gif_decode import decode_gif
            w, h, pixels = decode_gif(tex.data)
            img = bpy.data.images.new("lightmap_%s" % lm_id, width=w, height=h, alpha=True)
            img.pixels = pixels
        elif ext == "pcx":
            w, h, pixels = decode_pcx(tex.data)
            img = bpy.data.images.new("lightmap_%s" % lm_id, width=w, height=h, alpha=True)
            img.pixels = pixels
        else:
            path = _unique_texture_path(tmp_dir, "lightmap_%s" % lm_id, ext or "tga")
            with open(path, "wb") as f:
                f.write(tex.data)
            img = bpy.data.images.load(path, check_existing=False)
            if img.size[0] == 0:
                bpy.data.images.remove(img)
                raise ValueError("decoded to 0x0")
        # Lightmaps are baked illumination, not albedo.
        img.colorspace_settings.name = 'Non-Color'
        img.pack()
        log.write("  lightmap %s loaded (%s, %ix%i)" % (lm_id, ext, img.size[0], img.size[1]))
    except Exception as e:
        log.write("  lightmap %s FAILED: %s" % (lm_id, e))
        img = None

    lm_cache[lm_id] = img
    return img


def attach_lightmap(mat, lm_img, log):
    """Multiply the existing Base Color input by a lightmap sampled through
    the mesh's second UV layer."""
    if lm_img is None:
        return
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        return

    uv_node = nodes.new("ShaderNodeUVMap")
    uv_node.uv_map = "LightMap"
    uv_node.location = (bsdf.location.x - 1000, bsdf.location.y + 400)

    lm_node = nodes.new("ShaderNodeTexImage")
    lm_node.image = lm_img
    lm_node.location = (bsdf.location.x - 800, bsdf.location.y + 400)
    links.new(uv_node.outputs["UV"], lm_node.inputs["Vector"])

    mix = nodes.new("ShaderNodeMixRGB")
    mix.blend_type = 'MULTIPLY'
    mix.inputs["Fac"].default_value = 1.0
    mix.location = (bsdf.location.x - 200, bsdf.location.y + 250)

    base_in = bsdf.inputs["Base Color"]
    if base_in.is_linked:
        src = base_in.links[0].from_socket
        links.new(src, mix.inputs["Color1"])
    else:
        mix.inputs["Color1"].default_value = list(base_in.default_value)

    # Lightmaps in this era are typically 2x-scaled; boost so surfaces
    # don't come out uniformly dark.
    boost = nodes.new("ShaderNodeMixRGB")
    boost.blend_type = 'MULTIPLY'
    boost.inputs["Fac"].default_value = 1.0
    boost.inputs["Color2"].default_value = (2.0, 2.0, 2.0, 1.0)
    boost.location = (bsdf.location.x - 500, bsdf.location.y + 400)
    links.new(lm_node.outputs["Color"], boost.inputs["Color1"])
    links.new(boost.outputs["Color"], mix.inputs["Color2"])

    links.new(mix.outputs["Color"], base_in)


SUPPORTED_NATIVE = ("tga", "jpg", "dds")  # Blender can load these directly (dds support is platform-dependent)


class ImportLog:
    """Writes a plain-text log next to the imported .ldb so diagnostics are
    visible without launching Blender from a console."""

    def __init__(self, ldb_path):
        self.path = os.path.splitext(ldb_path)[0] + "_import_log.txt"
        self.lines = []

    def write(self, msg):
        self.lines.append(str(msg))

    def flush(self):
        try:
            with open(self.path, "w") as f:
                f.write("\n".join(self.lines))
        except Exception:
            pass
        return self.path


def _unique_texture_path(tmp_dir, base_name, ext):
    path = os.path.join(tmp_dir, base_name + "." + ext)
    i = 0
    while os.path.exists(path):
        i += 1
        path = os.path.join(tmp_dir, "%s_%i.%s" % (base_name, i, ext))
    return path


def decode_pcx_bytes(data):
    return decode_pcx(data)


def decode_pcx(data):
    """Minimal PCX decoder (RLE, 8-bit paletted and 24-bit planar), returning
    (width, height, list_of_floats_RGBA) suitable for bpy Image.pixels.
    Used so PCX works without requiring Pillow inside Blender's Python."""
    if len(data) < 128 or data[0] != 0x0A:
        raise ValueError("not a PCX file")

    bits_per_pixel = data[3]
    xmin = int.from_bytes(data[4:6], "little")
    ymin = int.from_bytes(data[6:8], "little")
    xmax = int.from_bytes(data[8:10], "little")
    ymax = int.from_bytes(data[10:12], "little")
    nplanes = data[65]
    bytes_per_line = int.from_bytes(data[66:68], "little")

    width = xmax - xmin + 1
    height = ymax - ymin + 1
    if width <= 0 or height <= 0:
        raise ValueError("bad PCX dimensions")

    total_bytes = bytes_per_line * nplanes * height

    # RLE decode
    out = bytearray()
    i = 128
    n = len(data)
    while len(out) < total_bytes and i < n:
        byte = data[i]
        i += 1
        if (byte & 0xC0) == 0xC0:
            count = byte & 0x3F
            if i >= n:
                break
            value = data[i]
            i += 1
            out.extend([value] * count)
        else:
            out.append(byte)
    out.extend(b"\x00" * max(0, total_bytes - len(out)))

    palette = None
    if bits_per_pixel == 8 and nplanes == 1:
        # 256-colour palette lives in the last 769 bytes, prefixed with 0x0C
        if len(data) >= 769 and data[-769] == 0x0C:
            palette = data[-768:]
        else:
            palette = bytes([(v // 3) for v in range(768)])

    pixels = [0.0] * (width * height * 4)
    inv = 1.0 / 255.0

    for y in range(height):
        row_start = y * bytes_per_line * nplanes
        # Blender image rows run bottom-to-top
        dst_y = height - 1 - y
        dst_row = dst_y * width * 4
        if nplanes == 3:
            r_off = row_start
            g_off = row_start + bytes_per_line
            b_off = row_start + bytes_per_line * 2
            for x in range(width):
                d = dst_row + x * 4
                pixels[d] = out[r_off + x] * inv
                pixels[d + 1] = out[g_off + x] * inv
                pixels[d + 2] = out[b_off + x] * inv
                pixels[d + 3] = 1.0
        else:
            for x in range(width):
                idx = out[row_start + x]
                d = dst_row + x * 4
                pixels[d] = palette[idx * 3] * inv
                pixels[d + 1] = palette[idx * 3 + 1] * inv
                pixels[d + 2] = palette[idx * 3 + 2] * inv
                pixels[d + 3] = 1.0

    return width, height, pixels


def win_basename(path):
    """Extract a filename from a path that may use Windows separators, even
    when running on Linux/macOS. The LDB files embed original Remedy dev
    paths like 'C:\\PROGRAM FILES\\MAX-FX TOOLS\\...\\BETON71A.JPG', which
    os.path.basename() will not split on a POSIX host."""
    if not path:
        return ""
    normalized = str(path).replace("\\", "/")
    return os.path.basename(normalized)


def sanitize_filename(name):
    """Strip anything that can't safely appear in a filename on disk."""
    keep = []
    for ch in name:
        if ch.isalnum() or ch in "._- ":
            keep.append(ch)
        else:
            keep.append("_")
    result = "".join(keep).strip() or "texture"
    return result[:80]


def build_external_texture_index(root_dir):
    """Walk a user-supplied texture folder (e.g. an extracted texture archive)
    and index every image by lowercase filename and by stem, so LDB records
    that reference missing/renamed files can still be matched."""
    index = {}
    if not root_dir or not os.path.isdir(root_dir):
        return index
    exts = {".jpg", ".jpeg", ".png", ".tga", ".pcx", ".gif", ".bmp", ".dds",
            ".tif", ".tiff"}
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for fn in filenames:
            stem, ext = os.path.splitext(fn)
            if ext.lower() not in exts:
                continue
            full = os.path.join(dirpath, fn)
            index.setdefault(fn.lower(), full)
            index.setdefault(stem.lower(), full)
    return index


def load_texture_image(tex_info, tmp_dir, cache, report, log, external_index=None):
    """tex_info: TextureInfo. Returns a bpy.types.Image or None."""
    if tex_info is None:
        return None
    if tex_info.file_path in cache:
        return cache[tex_info.file_path]

    # The LDB stores original Windows dev paths; take just the filename.
    raw_name = win_basename(tex_info.file_path)
    stem, _ = os.path.splitext(raw_name)
    safe_name = sanitize_filename(stem)
    ext = (tex_info.file_type_name or "").lower()
    size = len(tex_info.data) if tex_info.data else 0

    log.write("TEXTURE '%s' -> file='%s' type=%s bytes=%i"
              % (tex_info.file_path, raw_name, ext, size))

    # --- 1. Prefer embedded data when present ------------------------------
    if tex_info.data:
        if ext == "scx":
            log.write("   -> embedded data is SCX (unsupported), trying external folder")
        elif ext == "gif":
            # Blender cannot load GIF, so decode it here.
            try:
                from .gif_decode import decode_gif
                w, h, pixels = decode_gif(tex_info.data)
                img = bpy.data.images.new(safe_name, width=w, height=h, alpha=True)
                img.pixels = pixels
                img.pack()
                log.write("   -> OK: decoded embedded GIF %ix%i" % (w, h))
                cache[tex_info.file_path] = img
                return img
            except Exception as e:
                log.write("   -> FAILED decoding embedded GIF: %s" % e)
        elif ext.startswith("unknown_type_"):
            log.write("   -> unrecognised texture type (%s); trying external folder" % ext)
        elif ext == "pcx":
            try:
                w, h, pixels = decode_pcx(tex_info.data)
                img = bpy.data.images.new(safe_name, width=w, height=h, alpha=True)
                img.pixels = pixels
                img.pack()
                log.write("   -> OK: decoded embedded PCX %ix%i" % (w, h))
                cache[tex_info.file_path] = img
                return img
            except Exception as e:
                log.write("   -> FAILED decoding embedded PCX: %s" % e)
        else:
            out_path = _unique_texture_path(tmp_dir, safe_name, ext or "bin")
            try:
                with open(out_path, "wb") as f:
                    f.write(tex_info.data)
                img = bpy.data.images.load(out_path, check_existing=False)
                if img.size[0] == 0 or img.size[1] == 0:
                    log.write("   -> embedded data decoded to 0x0, discarding")
                    bpy.data.images.remove(img)
                else:
                    img.pack()
                    log.write("   -> OK: loaded embedded %s %ix%i" % (ext, img.size[0], img.size[1]))
                    cache[tex_info.file_path] = img
                    return img
            except Exception as e:
                log.write("   -> FAILED loading embedded data: %s" % e)
    else:
        log.write("   -> no embedded data in LDB record")

    # --- 2. Fall back to the user's external texture folder -----------------
    if external_index:
        candidate = (external_index.get(raw_name.lower())
                     or external_index.get(stem.lower()))
        if candidate:
            try:
                if candidate.lower().endswith(".gif"):
                    from .gif_decode import decode_gif
                    with open(candidate, "rb") as f:
                        w, h, pixels = decode_gif(f.read())
                    img = bpy.data.images.new(safe_name, width=w, height=h, alpha=True)
                    img.pixels = pixels
                elif candidate.lower().endswith(".pcx"):
                    with open(candidate, "rb") as f:
                        w, h, pixels = decode_pcx(f.read())
                    img = bpy.data.images.new(safe_name, width=w, height=h, alpha=True)
                    img.pixels = pixels
                else:
                    img = bpy.data.images.load(candidate, check_existing=True)
                    if img.size[0] == 0 or img.size[1] == 0:
                        raise ValueError("decoded to 0x0")
                img.pack()
                log.write("   -> OK: matched external file %s" % candidate)
                cache[tex_info.file_path] = img
                return img
            except Exception as e:
                log.write("   -> FAILED loading external '%s': %s" % (candidate, e))
        else:
            log.write("   -> no match for '%s' in external texture folder" % raw_name)

    cache[tex_info.file_path] = None
    return None


def build_material(mat_info, tmp_dir, texture_cache, report, log, external_index=None):
    log.write("MATERIAL %s (id=%s) diffuse=%s alpha=%s refl=%s gloss=%s detail=%s" % (
        mat_info.name, mat_info.id,
        mat_info.diffuse.file_path if mat_info.diffuse else None,
        mat_info.alpha.file_path if mat_info.alpha else None,
        mat_info.reflection.file_path if mat_info.reflection else None,
        mat_info.gloss.file_path if mat_info.gloss else None,
        mat_info.detail.file_path if mat_info.detail else None,
    ))

    existing = bpy.data.materials.get(mat_info.name)
    if existing is not None:
        return existing

    mat = bpy.data.materials.new(mat_info.name)
    mat.use_nodes = True
    nodes = mat.node_tree.nodes
    links = mat.node_tree.links
    bsdf = nodes.get("Principled BSDF")
    if bsdf is None:
        bsdf = nodes.new("ShaderNodeBsdfPrincipled")

    diffuse_img = load_texture_image(mat_info.diffuse, tmp_dir, texture_cache, report, log, external_index)
    if diffuse_img is not None:
        tex_node = nodes.new("ShaderNodeTexImage")
        tex_node.image = diffuse_img
        tex_node.location = (bsdf.location.x - 400, bsdf.location.y + 150)
        links.new(tex_node.outputs["Color"], bsdf.inputs["Base Color"])

        use_alpha_from_diffuse = mat_info.use_alpha and mat_info.alpha is None
        if use_alpha_from_diffuse:
            links.new(tex_node.outputs["Alpha"], bsdf.inputs["Alpha"])
            mat.blend_method = 'BLEND'
            mat.show_transparent_back = False

    alpha_img = load_texture_image(mat_info.alpha, tmp_dir, texture_cache, report, log, external_index)
    if alpha_img is not None:
        alpha_node = nodes.new("ShaderNodeTexImage")
        alpha_node.image = alpha_img
        alpha_node.location = (bsdf.location.x - 400, bsdf.location.y - 150)
        invert_node = nodes.new("ShaderNodeInvert")
        invert_node.location = (bsdf.location.x - 200, bsdf.location.y - 150)
        links.new(alpha_node.outputs["Color"], invert_node.inputs["Color"])
        links.new(invert_node.outputs["Color"], bsdf.inputs["Alpha"])
        mat.blend_method = 'BLEND'

    gloss_img = load_texture_image(mat_info.gloss, tmp_dir, texture_cache, report, log, external_index)
    if gloss_img is not None:
        gloss_node = nodes.new("ShaderNodeTexImage")
        gloss_node.image = gloss_img
        gloss_node.image.colorspace_settings.name = 'Non-Color'
        gloss_node.location = (bsdf.location.x - 400, bsdf.location.y - 350)
        invert_node = nodes.new("ShaderNodeInvert")
        invert_node.location = (bsdf.location.x - 200, bsdf.location.y - 350)
        links.new(gloss_node.outputs["Color"], invert_node.inputs["Color"])
        links.new(invert_node.outputs["Color"], bsdf.inputs["Roughness"])

    # Reflection / detail textures don't have an obvious 1:1 Principled BSDF
    # slot; they're loaded and left as unconnected image nodes so they're at
    # least visible/available for manual hookup.
    for extra in (mat_info.reflection, mat_info.detail):
        extra_img = load_texture_image(extra, tmp_dir, texture_cache, report, log, external_index)
        if extra_img is not None:
            extra_node = nodes.new("ShaderNodeTexImage")
            extra_node.image = extra_img
            extra_node.location = (bsdf.location.x - 400, bsdf.location.y - 550)

    return mat


def build_mesh_object(name, mesh_info, resolve_material, scale):
    verts = [conv_point(v, scale) for v in mesh_info.vertices]

    bm = bmesh.new()
    bm_verts = [bm.verts.new(v) for v in verts]
    bm.verts.ensure_lookup_table()

    uv_layer = bm.loops.layers.uv.new("UVMap")
    lm_layer = bm.loops.layers.uv.new("LightMap")

    # Build a lookup: vertex index -> per-poly material id, so we can set
    # bm.faces material_index after all faces exist (material list on the
    # mesh follows the order materials were first encountered).
    material_order = []
    material_index_of_id = {}

    idx_cursor = 0
    faces = []
    face_material_id = []
    poly_id_by_face_index = {}
    for poly_id, vcount in enumerate(mesh_info.vertices_per_poly):
        loop_vert_indices = mesh_info.indices[idx_cursor:idx_cursor + vcount]
        idx_cursor += vcount
        try:
            face_verts = [bm_verts[i] for i in loop_vert_indices]
            face = bm.faces.new(face_verts)
        except ValueError:
            # Degenerate / duplicate face -- skip rather than aborting the whole import.
            faces.append(None)
            face_material_id.append(None)
            continue
        for loop, vidx in zip(face.loops, loop_vert_indices):
            loop[uv_layer].uv = (mesh_info.us[vidx], mesh_info.vs[vidx])
            loop[lm_layer].uv = (mesh_info.lm_us[vidx], mesh_info.lm_vs[vidx])
        faces.append(face)

    # Map polygon id -> material id from mesh_info.materials {material_id: [poly_ids]}
    poly_to_material = {}
    for mat_id, poly_ids in mesh_info.materials.items():
        for pid in poly_ids:
            poly_to_material[pid] = mat_id

    for poly_id, face in enumerate(faces):
        if face is None:
            continue
        mat_key = poly_to_material.get(poly_id)
        if mat_key is None:
            continue
        if mat_key not in material_index_of_id:
            material_index_of_id[mat_key] = len(material_order)
            material_order.append(mat_key)
        face.material_index = material_index_of_id[mat_key]

    bm.normal_update()

    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()

    for mat_key in material_order:
        blender_mat = resolve_material(mat_key)
        if blender_mat is not None:
            mesh.materials.append(blender_mat)

    obj = bpy.data.objects.new(name, mesh)
    return obj


class IMPORT_OT_max_payne_ldb(bpy.types.Operator, ImportHelper):
    bl_idname = "import_scene.max_payne_ldb"
    bl_label = "Import Max Payne Level (.ldb)"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".ldb"
    filter_glob: StringProperty(default="*.ldb", options={'HIDDEN'})

    import_dynamic_meshes: BoolProperty(
        name="Import Dynamic Meshes",
        description="Import movable/dynamic props in addition to room geometry",
        default=True,
    )
    import_lightmaps: BoolProperty(
        name="Import Lightmaps",
        description="Apply the level's baked lightmap textures, multiplied over the "
                    "diffuse texture using the second UV layer",
        default=True,
    )
    import_lights: BoolProperty(
        name="Import Lights",
        description="Import the level's point lights and dynamic lights as Blender lamps",
        default=True,
    )
    light_energy_mult: FloatProperty(
        name="Light Strength",
        description="Multiplier applied to imported light wattage. Raise or lower this if "
                    "the level renders too dark or blown out",
        default=1.0,
        min=0.0,
        soft_max=20.0,
    )
    texture_dir: StringProperty(
        name="Texture Folder",
        description="Optional folder containing extracted game textures. Used when a "
                    "texture is missing from the LDB or fails to decode. Subfolders are searched",
        default="",
        subtype='DIR_PATH',
    )
    scale: FloatProperty(
        name="Scale",
        description="Uniform scale applied on import (game units are treated as meters by default)",
        default=1.0,
        min=0.0001,
    )

    def execute(self, context):
        tmp_dir = tempfile.mkdtemp(prefix="max_payne_ldb_")
        log = ImportLog(self.filepath)
        log.write("Max Payne LDB import: %s" % self.filepath)
        log.write("addon build: %s" % ADDON_BUILD)
        try:
            reader = MaxLBDReaderFactory.createReader(self.filepath)
            ldb = reader.parse()
        except Exception:
            self.report({'ERROR'}, "Failed to parse LDB file:\n" + traceback.format_exc())
            return {'CANCELLED'}

        level_name = os.path.splitext(os.path.basename(self.filepath))[0]
        root_collection = bpy.data.collections.new(level_name)
        context.scene.collection.children.link(root_collection)
        rooms_collection = bpy.data.collections.new("Rooms")
        root_collection.children.link(rooms_collection)
        dynamic_collection = None
        if self.import_dynamic_meshes:
            dynamic_collection = bpy.data.collections.new("Dynamic Meshes")
            root_collection.children.link(dynamic_collection)

        # -- Texture inventory (diagnostics) ------------------------------
        try:
            from collections import Counter
            fmt_counter = Counter()
            for t in ldb.getTextures():
                try:
                    fmt_counter[t.getFileTypeName()] += 1
                except Exception:
                    fmt_counter["<unknown type %s>" % getattr(t, "file_type", "?")] += 1
            log.write("Level contains %i textures by format: %s"
                      % (len(ldb.getTextures()), dict(fmt_counter)))
            log.write("Level contains %i materials" % len(ldb.getMaterials()))
            log.write("-" * 60)
        except Exception:
            log.write("Could not inventory textures:\n" + traceback.format_exc())

        # -- Materials --------------------------------------------------
        external_index = build_external_texture_index(bpy.path.abspath(self.texture_dir) if self.texture_dir else "")
        if external_index:
            log.write("External texture folder indexed: %i entries from %s"
                      % (len(external_index), self.texture_dir))
        else:
            log.write("No external texture folder set (or none found)")
        log.write("-" * 60)

        texture_cache = {}
        lm_cache = {}
        blender_materials = {}
        mat_infos = get_materials(ldb)
        mat_info_by_id = {m.id: m for m in mat_infos}

        def resolve_material(mat_key):
            """mat_key is (material_id, lightmap_id). A separate Blender
            material is created per pairing so differing lightmaps on the same
            base material stay correct."""
            if mat_key in blender_materials:
                return blender_materials[mat_key]
            mat_id, lm_id = mat_key
            info = mat_info_by_id.get(mat_id)
            if info is None:
                blender_materials[mat_key] = None
                return None
            variant = MaterialInfo(info.id, info.name if lm_id is None
                                   else "%s_lm%s" % (info.name, lm_id))
            variant.diffuse = info.diffuse
            variant.alpha = info.alpha
            variant.reflection = info.reflection
            variant.gloss = info.gloss
            variant.detail = info.detail
            variant.use_alpha = info.use_alpha
            mat = build_material(variant, tmp_dir, texture_cache, self.report, log, external_index)
            if self.import_lightmaps and lm_id is not None:
                lm_img = load_lightmap_image(ldb, lm_id, tmp_dir, lm_cache, log)
                attach_lightmap(mat, lm_img, log)
            blender_materials[mat_key] = mat
            return mat

        # -- Rooms --------------------------------------------------------
        room_count = len(ldb.getRooms())
        room_game_transforms = {}
        for room_id in range(room_count):
            try:
                mesh_info = get_room_static_mesh(ldb, room_id)
            except Exception:
                self.report({'WARNING'}, "Room %i failed to build:\n%s" % (room_id, traceback.format_exc()))
                continue
            room_game_transforms[room_id] = mesh_info.transform
            obj = build_mesh_object("Room_%03d" % room_id, mesh_info, resolve_material, self.scale)
            obj.matrix_world = conv_transform(mesh_info.transform, self.scale)
            rooms_collection.objects.link(obj)

        # -- Dynamic meshes -------------------------------------------------
        if self.import_dynamic_meshes:
            for room_id in range(room_count):
                try:
                    dynamic_list = list(get_dynamic_meshes_by_room(ldb, room_id))
                except Exception:
                    self.report({'WARNING'}, "Dynamic meshes for room %i failed:\n%s" % (room_id, traceback.format_exc()))
                    continue
                for dyn_idx, mesh_info in enumerate(dynamic_list):
                    use_room_transform = getattr(mesh_info, "use_room_transform", True)
                    if use_room_transform and room_id in room_game_transforms:
                        world_rows = compose_game_transform(room_game_transforms[room_id], mesh_info.transform)
                    else:
                        world_rows = mesh_info.transform
                    obj = build_mesh_object("Room_%03d_Dyn_%03d" % (room_id, dyn_idx), mesh_info, resolve_material, self.scale)
                    obj.matrix_world = conv_transform(world_rows, self.scale)
                    dynamic_collection.objects.link(obj)

        # -- Lights ---------------------------------------------------------
        lights_created = 0
        if self.import_lights:
            lights_collection = bpy.data.collections.new("Lights")
            root_collection.children.link(lights_collection)
            try:
                light_infos = get_lights(ldb, log)
                log.write("-" * 60)
                log.write("Found %i lights" % len(light_infos))
                for li in light_infos:
                    if li.use_room_transform and li.room_id in room_game_transforms:
                        world_rows = compose_game_transform(
                            room_game_transforms[li.room_id], li.transform)
                    else:
                        world_rows = li.transform
                    if world_rows is None:
                        log.write("  light '%s' has no transform, skipping" % li.name)
                        continue
                    obj = build_light_object(li, world_rows, self.scale,
                                             self.light_energy_mult, log)
                    lights_collection.objects.link(obj)
                    lights_created += 1
                    log.write("  light '%s' rgb=%s intensity=%.3f falloff=%.3f room=%s"
                              % (li.name, tuple(round(c, 3) for c in li.color),
                                 li.intensity, li.falloff, li.room_id))
            except Exception:
                log.write("Light import failed:\n" + traceback.format_exc())

        images_loaded = sum(1 for v in texture_cache.values() if v is not None)
        images_failed = sum(1 for v in texture_cache.values() if v is None)
        log.write("-" * 60)
        log.write("SUMMARY: %i rooms, %i materials, %i textures loaded, %i textures failed, %i lights"
                  % (room_count, len(mat_infos), images_loaded, images_failed, lights_created))
        log_path = log.flush()

        self.report({'INFO'}, "Imported %i rooms, %i lights, %i textures OK / %i failed. Log: %s"
                    % (room_count, lights_created, images_loaded, images_failed, log_path))
        return {'FINISHED'}


class IMPORT_OT_max_payne_kf2(bpy.types.Operator, ImportHelper):
    """Import Max Payne model files. KFS and SKD share the KF2 container
    format, so all three extensions are handled by the same reader."""
    bl_idname = "import_scene.max_payne_kf2"
    bl_label = "Import Max Payne Model (.kf2/.kfs/.skd)"
    # Select the .KFS, .SKD and animation .KF2 together: a character's mesh,
    # skin weights and skeleton each live in a different file.
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".kf2"
    filter_glob: StringProperty(default="*.kf2;*.kfs;*.skd;*.KF2;*.KFS;*.SKD",
                                options={'HIDDEN'})

    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    texture_dir: StringProperty(
        name="Texture Folder",
        description="Folder containing the model's textures. KF2 files store bare "
                    "texture names, so this is required for textures to appear. "
                    "Subfolders are searched",
        default="",
        subtype='DIR_PATH',
    )
    import_materials: BoolProperty(
        name="Import Materials",
        description="Build materials and preserve animated (facial expression) texture frame sets",
        default=True,
    )
    rest_pose_file: StringProperty(
        name="Rest Pose (.kf2)",
        description="Optional pose file giving the skeleton's neutral bind pose. "
                    "For the standard human rig this is Widepose.kf2 in "
                    "Data/database/skeletons/default_skeleton/anim. Without it the "
                    "first frame of an animation is used, which is a posed frame "
                    "and can skew the rig. Files named *pose* among the selection "
                    "are detected automatically",
        default="",
        subtype='FILE_PATH',
    )
    import_skeleton: BoolProperty(
        name="Import Skeleton & Animation",
        description="Build an armature from the skin's bone list and load keyframe animations",
        default=True,
    )
    scale: FloatProperty(
        name="Scale",
        description="Uniform scale applied on import",
        default=1.0,
        min=0.0001,
    )

    def execute(self, context):
        from . import kf2_import

        paths = []
        if self.files:
            for f in self.files:
                if f.name:
                    paths.append(os.path.join(self.directory, f.name))
        if not paths:
            paths = [self.filepath]

        log = ImportLog(paths[0])
        log.write("Max Payne model import (%i file(s))" % len(paths))
        log.write("addon build: %s" % ADDON_BUILD)
        log.write("-" * 60)

        search_index = build_external_texture_index(
            bpy.path.abspath(self.texture_dir) if self.texture_dir else "")
        if search_index:
            log.write("Texture folder indexed: %i entries" % len(search_index))
        else:
            log.write("No texture folder set -- textures will not resolve, "
                      "since KF2 stores only bare texture names")
        log.write("-" * 60)

        coll_name = os.path.splitext(os.path.basename(paths[0]))[0]
        collection = bpy.data.collections.new(coll_name)
        context.scene.collection.children.link(collection)

        total_objs = 0
        total_skins = 0
        total_anims = 0
        arm = None
        try:
            kwargs = dict(search_index=search_index,
                          import_materials=self.import_materials,
                          import_skeleton=self.import_skeleton,
                          rest_pose_path=self.rest_pose_file)
            # Pass by keyword and drop anything an older loaded copy of the
            # module does not accept, so a stale submodule degrades instead of
            # raising a positional-argument TypeError.
            import inspect
            accepted = set(inspect.signature(kf2_import.import_character).parameters)
            dropped = [k for k in kwargs if k not in accepted]
            for k in dropped:
                kwargs.pop(k)
            if dropped:
                log.write("WARNING: loaded kf2_import is out of date; ignoring %s. "
                          "Restart Blender to pick up the current addon."
                          % ", ".join(dropped))
            objs, skins, arm, animated = kf2_import.import_character(
                paths, self.scale, log, collection, **kwargs)
            total_objs = len(objs)
            total_skins = skins
            total_anims = animated
        except Exception as exc:
            log.write("FAILED:\n%s" % traceback.format_exc())
            # Surface the actual error in the UI; "see log" alone forces a
            # round trip just to learn what broke.
            self.report({'ERROR'}, "Import failed: %s: %s"
                        % (type(exc).__name__, str(exc)[:180]))

        log.write("-" * 60)
        log.write("SUMMARY: %i objects, %i skinned meshes, %i animated bones from %i file(s)"
                  % (total_objs, total_skins, total_anims, len(paths)))
        log_path = log.flush()

        made_armature = arm is not None
        if total_objs > 0 and total_skins > 0 and made_armature and self.import_skeleton:
            self.report({'INFO'}, "Applied skin weights to %i mesh(es) and bound them "
                                  "to '%s'" % (total_objs, arm.name))
        elif total_objs == 0 and made_armature:
            # A pose file on its own contains no mesh -- building the skeleton
            # from it is the expected result, not a failure.
            nbones = len(arm.data.bones)
            self.report({'INFO'}, "Imported skeleton only: %i bones. Add the .KFS and "
                                  ".SKD to get the character mesh." % nbones)
        elif total_skins > 0 and total_objs == 0:
            # Weights were applied to a mesh already in the scene, but no
            # armature existed to bind to.
            self.report({'WARNING'}, "Skin weights applied, but no skeleton found. "
                                     "Import Widepose.kf2 first, then re-import this "
                                     ".SKD to bind it.")
        elif total_objs == 0:
            self.report({'WARNING'}, "Nothing imported. See log: %s" % log_path)
        else:
            self.report({'INFO'}, "Imported %i objects (%i skinned, %i animated bones). Log: %s"
                        % (total_objs, total_skins, total_anims, log_path))
        return {'FINISHED'}


class IMPORT_OT_max_payne_anim(bpy.types.Operator, ImportHelper):
    """Load one or more .kf2 animations onto an already-imported armature.

    Animations are separate .kf2 files layered onto a character's rig. Each
    selected file becomes its own Blender Action, so a whole animation library
    can be stacked on one armature and switched from the Action editor or NLA,
    rather than re-importing the character for every clip."""
    bl_idname = "import_scene.max_payne_anim"
    bl_label = "Add Max Payne Animation (.kf2)"
    bl_options = {'PRESET', 'UNDO'}

    filename_ext = ".kf2"
    filter_glob: StringProperty(default="*.kf2;*.KF2", options={'HIDDEN'})

    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement, options={'HIDDEN', 'SKIP_SAVE'})
    directory: StringProperty(subtype='DIR_PATH', options={'HIDDEN', 'SKIP_SAVE'})

    scale: FloatProperty(
        name="Scale",
        description="Must match the scale the character was imported with",
        default=1.0,
        min=0.0001,
    )
    skip_static_bones: BoolProperty(
        name="Only Animated Bones",
        description="Skip bones that hold a single keyframe, leaving them free for "
                    "another NLA strip to drive. Off by default, and usually should "
                    "stay off: a single-key bone is normally a deliberate held pose, "
                    "not a gap. The 'w_' wounded clips park the arms on one key "
                    "precisely because the character clutches his side while limping, "
                    "so skipping those bones loses the pose",
        default=False,
    )
    push_to_nla: BoolProperty(
        name="Push to NLA",
        description="Stash each action as an NLA strip. Recommended when loading "
                    "several animations, so none are lost when the active action changes",
        default=True,
    )
    set_fake_user: BoolProperty(
        name="Protect Actions",
        description="Mark actions with a fake user so they survive saving and reloading "
                    "even when not assigned",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'ARMATURE'

    def execute(self, context):
        from . import kf2_skeleton
        from max_payne_sdk.max_kf2 import MaxKF2Reader

        arm_obj = context.active_object
        if arm_obj is None or arm_obj.type != 'ARMATURE':
            self.report({'ERROR'}, "Select the character's armature first")
            return {'CANCELLED'}

        paths = []
        if self.files:
            for f in self.files:
                if f.name:
                    paths.append(os.path.join(self.directory, f.name))
        if not paths:
            paths = [self.filepath]

        log = ImportLog(paths[0])
        log.write("Max Payne animation load onto '%s' (%i file(s))"
                  % (arm_obj.name, len(paths)))
        log.write("addon build: %s" % ADDON_BUILD)
        log.write("-" * 60)

        rig_bones = [b.name for b in arm_obj.data.bones]
        loaded = 0
        skipped = 0

        original_action = None
        if arm_obj.animation_data:
            original_action = arm_obj.animation_data.action

        for path in paths:
            clip = os.path.splitext(os.path.basename(path))[0]
            try:
                kf2 = MaxKF2Reader().parse(path)
            except Exception:
                log.write("%s: PARSE FAILED\n%s" % (clip, traceback.format_exc()))
                skipped += 1
                continue

            anims = kf2.getKeyframeAnimations()
            if not anims:
                log.write("%s: contains no animation chunks, skipped" % clip)
                skipped += 1
                continue

            # Bone placement per frame is accumulated from the animation's own
            # parent-relative transforms, so no rest-pose file is needed here --
            # the armature already carries the rest pose.
            table = kf2_skeleton.build_bone_table(anims, None, None)

            anim_bones = set(table)
            matched = [b for b in rig_bones if b in anim_bones]
            # The two rigs in the game share four bone names (Gun, Head, Pelvis,
            # Torso), so "at least one match" is not a safe test -- a rat clip
            # would partially apply to a human rig and produce garbage. Require
            # most of the animation's bones to exist on the target instead.
            coverage = len(matched) / float(len(anim_bones)) if anim_bones else 0.0
            # Threshold measured against the real data: a matching rig scores
            # 100%, a rat clip on the human rig scores exactly 50% (the four
            # shared names) and a human clip on the rat rig scores 14%.
            # 0.75 separates them with margin on both sides.
            if coverage < 0.75:
                log.write("%s: only %d of its %i bones exist on '%s' (%.0f%%) -- this "
                          "animation is for a different rig, skipped"
                          % (clip, len(matched), len(anim_bones), arm_obj.name,
                             coverage * 100))
                skipped += 1
                continue
            unmatched = sorted(anim_bones - set(rig_bones))
            if unmatched:
                log.write("%s: %i bone(s) not on this rig, ignored: %s"
                          % (clip, len(unmatched), ", ".join(unmatched[:8])))

            try:
                targets = matched
                if self.skip_static_bones:
                    moving = [b for b in matched if len(table[b]["keys"]) > 1]
                    parked = [b for b in matched if len(table[b]["keys"]) <= 1]
                    if parked:
                        log.write("%s: %i bone(s) hold a single key and were left "
                                  "unkeyed for layering: %s"
                                  % (clip, len(parked), ", ".join(parked[:10])))
                        log.write("   NOTE: a held pose (e.g. a wounded character "
                                  "clutching his side) also reads as a single key, "
                                  "so check this is really what you want.")
                    targets = moving or matched
                animated, last = kf2_skeleton.apply_animation(
                    arm_obj, targets, table, self.scale, clip, log,
                    use_stored_rest=True)
            except Exception:
                log.write("%s: FAILED\n%s" % (clip, traceback.format_exc()))
                skipped += 1
                continue

            action = arm_obj.animation_data.action
            if action is not None:
                if self.set_fake_user:
                    action.use_fake_user = True
                action["mp_source_file"] = os.path.basename(path)
                action["mp_frame_end"] = last
                if self.push_to_nla:
                    try:
                        track = arm_obj.animation_data.nla_tracks.new()
                        track.name = clip
                        track.strips.new(clip, 1, action)
                        arm_obj.animation_data.action = None
                    except Exception:
                        log.write("  could not push '%s' to NLA" % clip)

            log.write("%s: %i bones keyed, %i frames" % (clip, animated, last))
            loaded += 1

        # Leave the last loaded clip active when nothing was pushed to NLA,
        # otherwise restore whatever was assigned before.
        if self.push_to_nla and arm_obj.animation_data:
            arm_obj.animation_data.action = original_action

        try:
            from . import kf2_import
            kf2_import.auto_rig_scene(log, self.scale)
        except Exception:
            pass

        log.write("-" * 60)
        log.write("SUMMARY: %i animation(s) loaded, %i skipped" % (loaded, skipped))
        log_path = log.flush()

        if loaded == 0:
            self.report({'WARNING'}, "No animations loaded. Log: %s" % log_path)
        else:
            where = "NLA tracks" if self.push_to_nla else "actions"
            self.report({'INFO'}, "Loaded %i animation(s) as %s on '%s'%s"
                        % (loaded, where, arm_obj.name,
                           " (%i skipped)" % skipped if skipped else ""))
        return {'FINISHED'}


class OBJECT_OT_max_payne_rig(bpy.types.Operator):
    """Bind Max Payne meshes in this scene to their skeleton.

    Runs automatically after every import, so it is only needed to repair a
    scene assembled earlier or by hand."""
    bl_idname = "object.max_payne_rig"
    bl_label = "Rig Max Payne Model to Skeleton"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        from . import kf2_import

        class _Log:
            def __init__(self):
                self.lines = []

            def write(self, msg):
                self.lines.append(str(msg))

        log = _Log()
        try:
            bound = kf2_import.auto_rig_scene(log, 1.0)
        except Exception:
            self.report({'ERROR'}, "Rigging failed: %s" % traceback.format_exc(limit=1))
            return {'CANCELLED'}

        armatures = [o for o in bpy.data.objects if o.type == 'ARMATURE']
        weighted = [o for o in bpy.data.objects
                    if o.type == 'MESH' and o.vertex_groups]

        if not armatures:
            self.report({'WARNING'}, "No armature in the scene. Import a pose file "
                                     "(Widepose.kf2, or rat_stand_pose.kf2) first.")
        elif not weighted:
            self.report({'WARNING'}, "No mesh has vertex groups. Import the .SKD to "
                                     "get skin weights.")
        elif bound:
            self.report({'INFO'}, "Rigged %i mesh(es) to the skeleton." % bound)
        else:
            self.report({'INFO'}, "Already rigged - nothing to change.")
        return {'FINISHED'}


def menu_func_import(self, context):
    self.layout.operator(IMPORT_OT_max_payne_ldb.bl_idname, text="Max Payne Level (.ldb)")


def menu_func_import_kf2(self, context):
    self.layout.operator(IMPORT_OT_max_payne_kf2.bl_idname,
                         text="Max Payne Model (.kf2/.kfs/.skd)")


def menu_func_import_anim(self, context):
    self.layout.operator(IMPORT_OT_max_payne_anim.bl_idname,
                         text="Max Payne Animation onto Armature (.kf2)")


def menu_func_rig(self, context):
    self.layout.operator(OBJECT_OT_max_payne_rig.bl_idname,
                         text="Rig Max Payne Model to Skeleton")


def _reload_submodules():
    """Force-reload this addon's submodules.

    Blender re-executes __init__.py when an addon is enabled, but `from . import
    kf2_import` returns whatever is already in sys.modules. After an in-place
    update that leaves __init__.py new and the submodules stale -- which
    surfaces as impossible-looking errors, e.g. a TypeError reporting an old
    function signature that no longer exists in the source on disk.

    Reloading here means updating the addon no longer requires restarting
    Blender.
    """
    import importlib
    import sys as _sys

    package = __name__
    for name in ("gif_decode", "kf2_material", "kf2_skeleton", "kf2_import"):
        full = "%s.%s" % (package, name)
        module = _sys.modules.get(full)
        if module is not None:
            try:
                importlib.reload(module)
            except Exception:
                # A failed reload must not prevent the addon from registering.
                _sys.modules.pop(full, None)

    # The bundled SDK is a separate top-level package; drop its cached modules
    # so a corrected copy (e.g. the texture type table) takes effect too.
    for full in [m for m in list(_sys.modules) if m.startswith("max_payne_sdk")]:
        _sys.modules.pop(full, None)


def register():
    _reload_submodules()
    bpy.utils.register_class(IMPORT_OT_max_payne_ldb)
    bpy.utils.register_class(IMPORT_OT_max_payne_kf2)
    bpy.utils.register_class(IMPORT_OT_max_payne_anim)
    bpy.utils.register_class(OBJECT_OT_max_payne_rig)
    bpy.types.VIEW3D_MT_object.append(menu_func_rig)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_kf2)
    bpy.types.TOPBAR_MT_file_import.append(menu_func_import_anim)


def unregister():
    bpy.types.VIEW3D_MT_object.remove(menu_func_rig)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_anim)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import_kf2)
    bpy.types.TOPBAR_MT_file_import.remove(menu_func_import)
    bpy.utils.unregister_class(OBJECT_OT_max_payne_rig)
    bpy.utils.unregister_class(IMPORT_OT_max_payne_anim)
    bpy.utils.unregister_class(IMPORT_OT_max_payne_kf2)
    bpy.utils.unregister_class(IMPORT_OT_max_payne_ldb)


if __name__ == "__main__":
    register()
