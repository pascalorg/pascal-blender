"""Shared bpy helpers: build context, mesh construction, data layer stamping.

Units are meters (1 Pascal meter = 1 Blender unit), axes are Blender Z-up;
all Pascal->Blender conversion happens in core.coords before geometry is built.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

import bmesh
import bpy
from mathutils.geometry import tessellate_polygon
from mathutils import Vector

from ..core import materials as core_materials
from ..core.parse import SceneData

PASCAL_PREFIX = "pascal_"


@dataclass
class BuildContext:
    scene_data: SceneData
    nodes: Dict[str, Any]                  # post-migration nodes (geometry source)
    migrated_ids: set
    options: Dict[str, Any]
    import_collection: Optional["bpy.types.Collection"] = None
    level_origins: Dict[str, "bpy.types.Object"] = field(default_factory=dict)
    level_offsets: Dict[str, float] = field(default_factory=dict)
    cutter_collections: Dict[str, "bpy.types.Collection"] = field(default_factory=dict)
    material_cache: Dict[Tuple, "bpy.types.Material"] = field(default_factory=dict)
    anchors: Dict[str, "bpy.types.ID"] = field(default_factory=dict)
    special: Dict[str, "bpy.types.Collection"] = field(default_factory=dict)
    report: List[Tuple[str, str]] = field(default_factory=list)
    created_ids: List["bpy.types.ID"] = field(default_factory=list)

    def note(self, level: str, message: str) -> None:
        self.report.append((level, message))

    def verbatim_node(self, node_id: str) -> Dict[str, Any]:
        """Pre-migration node dict (the lossless source of truth)."""
        return self.scene_data.nodes.get(node_id, self.nodes.get(node_id, {}))

    def track(self, datablock: "bpy.types.ID") -> "bpy.types.ID":
        self.created_ids.append(datablock)
        return datablock


def display_name(node: Dict[str, Any]) -> str:
    """Human-readable object name: '<name-or-TypeLabel> <first-4-of-suffix>'.

    Identity never lives in names (63-byte truncation, .001 suffixes) — it
    lives in the pascal_id custom property.
    """
    name = node.get("name")
    if not name:
        t = str(node.get("type", "node"))
        name = t.replace("-", " ").title()
    nid = str(node.get("id", ""))
    suffix = nid.split("_", 1)[1][:4] if "_" in nid else nid[:4]
    label = f"{name} {suffix}" if suffix else str(name)
    raw = label.encode("utf-8")[:63]
    return raw.decode("utf-8", errors="ignore")


def stamp_datalayer(
    datablock: "bpy.types.ID",
    node: Dict[str, Any],
    params: Optional[Dict[str, Any]] = None,
    migrated: bool = False,
) -> None:
    """Write the per-node lossless data layer onto an anchor datablock.

    ``pascal_json`` is the verbatim PRE-migration node as a string (string,
    not dict idprop: ID properties can't hold heterogeneous arrays, overflow
    at 2^31, and cap key names at 63 chars).
    """
    datablock["pascal_id"] = str(node.get("id", ""))
    datablock["pascal_type"] = str(node.get("type", "unknown"))
    datablock["pascal_json"] = json.dumps(node, ensure_ascii=False)
    if params:
        safe: Dict[str, Any] = {}
        for key, value in params.items():
            if isinstance(value, (int, float, str, bool)):
                safe[key] = value
            elif isinstance(value, (list, tuple)) and value and all(
                isinstance(x, (int, float)) for x in value
            ):
                safe[key] = [float(x) for x in value]
        if safe:
            datablock["pascal_params"] = safe
    if migrated:
        datablock["pascal_migrated"] = True


def link_object(ctx: BuildContext, obj: "bpy.types.Object", collection: "bpy.types.Collection") -> None:
    collection.objects.link(obj)
    ctx.track(obj)


def new_collection(ctx: BuildContext, name: str, parent: "bpy.types.Collection") -> "bpy.types.Collection":
    coll = bpy.data.collections.new(name)
    parent.children.link(coll)
    ctx.track(coll)
    return coll


def _tessellate(outer: List[Tuple[float, float]], holes: List[List[Tuple[float, float]]]):
    loops = [[Vector((x, y, 0.0)) for x, y in outer]]
    for hole in holes:
        loops.append([Vector((x, y, 0.0)) for x, y in hole])
    tris = tessellate_polygon(loops)
    verts: List[Tuple[float, float, float]] = []
    for loop in loops:
        verts.extend((v.x, v.y, 0.0) for v in loop)
    return verts, tris


def mesh_from_polygon(
    name: str,
    outer: Sequence[Tuple[float, float]],
    holes: Sequence[Sequence[Tuple[float, float]]] = (),
    extrude: float = 0.0,
    z_base: float = 0.0,
) -> "bpy.types.Mesh":
    """Polygon-with-holes -> (optionally extruded) solid mesh.

    ``outer``/``holes`` are Blender ground-plane (x, y) coords. Extrusion goes
    +Z from z_base. Pipeline per spec 07 §1: tessellate_polygon -> from_pydata
    -> validate -> bmesh dissolve_limit -> extrude -> recalc normals.
    """
    outer = [(float(x), float(y)) for x, y in outer]
    holes = [[(float(x), float(y)) for x, y in h] for h in holes if len(h) >= 3]
    mesh = bpy.data.meshes.new(name)
    if len(outer) < 3:
        return mesh

    verts, tris = _tessellate(outer, holes)
    mesh.from_pydata(verts, [], [tuple(t) for t in tris])
    mesh.validate()

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=1e-6)
    bmesh.ops.dissolve_limit(
        bm, angle_limit=0.0175, verts=bm.verts[:], edges=bm.edges[:]
    )
    if extrude:
        geom = bmesh.ops.extrude_face_region(bm, geom=bm.faces[:])
        extruded_verts = [g for g in geom["geom"] if isinstance(g, bmesh.types.BMVert)]
        bmesh.ops.translate(bm, vec=(0, 0, extrude), verts=extruded_verts)
    if z_base:
        bmesh.ops.translate(bm, vec=(0, 0, z_base), verts=bm.verts[:])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    mesh.validate()
    return mesh


def mesh_from_meshdata(name: str, meshdata) -> "bpy.types.Mesh":
    """(verts, faces, face_material_indices) -> Mesh with material indices."""
    verts, faces, mat_indices = meshdata
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([tuple(v) for v in verts], [], [tuple(f) for f in faces])
    mesh.validate()
    if mat_indices and len(mat_indices) == len(mesh.polygons):
        for poly, idx in zip(mesh.polygons, mat_indices):
            poly.material_index = int(idx)
    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()
    return mesh


def get_material(
    ctx: BuildContext,
    material_json: Optional[Dict[str, Any]],
    node_type: str = "",
    resolved: Optional[Dict[str, Any]] = None,
) -> "bpy.types.Material":
    """Deduplicated Principled BSDF material for a resolved 6-tuple."""
    if resolved is None:
        resolved = core_materials.resolve_material(material_json, node_type)
    base_key = core_materials.resolved_tuple(resolved)
    texture_json = (material_json or {}).get("texture")
    key = (base_key, json.dumps(texture_json, sort_keys=True) if texture_json else None)
    cached = ctx.material_cache.get(key)
    if cached is not None:
        return cached

    mat = bpy.data.materials.new(core_materials.material_name(material_json, resolved))
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    r, g, b = core_materials.hex_to_linear_rgb(base_key[0])
    alpha = base_key[3]
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Roughness"].default_value = base_key[1]
    bsdf.inputs["Metallic"].default_value = base_key[2]
    bsdf.inputs["Alpha"].default_value = alpha
    transparent = base_key[4]
    if transparent or alpha < 1.0:
        mat.surface_render_method = "BLENDED"
    mat.use_backface_culling = base_key[5] == "front"
    mat.diffuse_color = (r, g, b, alpha)
    if ctx.options.get("physical_glass") and transparent:
        bsdf.inputs["Transmission Weight"].default_value = 1.0
        bsdf.inputs["IOR"].default_value = 1.45

    texture = (material_json or {}).get("texture")
    if texture and ctx.options.get("apply_texture_field", True):
        _wire_texture(ctx, mat, bsdf, texture)

    mat["pascal_material_json"] = json.dumps(material_json or {}, ensure_ascii=False)
    mat["pascal_resolved"] = json.dumps(base_key)
    ctx.material_cache[key] = mat
    ctx.track(mat)
    return mat


def _wire_texture(ctx: BuildContext, mat, bsdf, texture: Dict[str, Any]) -> None:
    """material.texture (unused by the editor, honored here): UV Map ->
    Mapping (scale = repeat * scale) -> Image Texture -> Base Color. The
    image itself is a checker placeholder when the URL isn't fetchable."""
    tree = mat.node_tree
    uv = tree.nodes.new("ShaderNodeUVMap")
    mapping = tree.nodes.new("ShaderNodeMapping")
    tex = tree.nodes.new("ShaderNodeTexImage")
    repeat = texture.get("repeat") or [1.0, 1.0]
    scale = float(texture.get("scale") or 1.0)
    mapping.inputs["Scale"].default_value = (repeat[0] * scale, repeat[1] * scale, 1.0)
    tex.extension = "REPEAT"

    url = str(texture.get("url", ""))
    image = bpy.data.images.new(f"pascal_tex_{hash(url) & 0xFFFF:04x}", 8, 8)
    image.generated_type = "COLOR_GRID"
    image.source = "GENERATED"
    image["pascal_texture_url"] = url
    tex.image = image
    ctx.track(image)
    ctx.note("INFO", f"texture field {url}: placeholder image (relink manually)")

    tree.links.new(uv.outputs["UV"], mapping.inputs["Vector"])
    tree.links.new(mapping.outputs["Vector"], tex.inputs["Vector"])
    tree.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])


def mesh_volume(obj: "bpy.types.Object", evaluated: bool = True) -> float:
    """Signed volume of an object's (optionally modifier-evaluated) mesh."""
    if evaluated:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        mesh = obj.evaluated_get(depsgraph).to_mesh()
    else:
        mesh = obj.data
    bm = bmesh.new()
    bm.from_mesh(mesh)
    volume = bm.calc_volume(signed=True)
    bm.free()
    if evaluated:
        obj.evaluated_get(bpy.context.evaluated_depsgraph_get()).to_mesh_clear()
    return volume
