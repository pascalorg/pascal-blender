"""Builders for the newer editor kinds (fence, shelf, spawn) and the
pascal:trees plugin kinds (trees:tree, trees:grass)."""
from __future__ import annotations

import math
from typing import Any, Dict, List

import bpy

from ..core import coords, plugin_geometry, schema
from ..core.materials import hex_to_linear_rgb
from .common import BuildContext, display_name, get_material, link_object, stamp_datalayer


def _boxes_to_meshdata(boxes: List[plugin_geometry.Box]):
    """Pascal-local box list -> Blender-local (verts, faces, mat_indices).
    Pascal (x, y, z) -> Blender (x, -z, y)."""
    verts, faces, mats = [], [], []
    for (cx, cy, cz), (sx, sy, sz), mat in boxes:
        bx, by, bz = cx, -cz, cy          # center
        hx, hy, hz = sx / 2, sz / 2, sy / 2  # sizes: pascal z -> blender y
        base = len(verts)
        verts.extend([
            (bx - hx, by - hy, bz - hz), (bx + hx, by - hy, bz - hz),
            (bx + hx, by + hy, bz - hz), (bx - hx, by + hy, bz - hz),
            (bx - hx, by - hy, bz + hz), (bx + hx, by - hy, bz + hz),
            (bx + hx, by + hy, bz + hz), (bx - hx, by + hy, bz + hz),
        ])
        for f in [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]:
            faces.append(tuple(base + i for i in f))
            mats.append(mat)
    return verts, faces, mats


def _pascal_meshdata_to_blender(meshdata):
    """(verts in Pascal Y-up, faces, mats) -> Blender Z-up."""
    verts, faces, mats = meshdata
    return [(v[0], -v[2], v[1]) for v in verts], faces, mats


def _make_object(ctx: BuildContext, node_id: str, node: Dict[str, Any], meshdata, mesh_key=None):
    """Create (or reuse via mesh_key cache) a mesh object for a node."""
    from .common import mesh_from_meshdata

    cache: Dict[Any, "bpy.types.Mesh"] = ctx.options.setdefault("_plugin_mesh_cache", {})
    mesh = cache.get(mesh_key) if mesh_key else None
    if mesh is None:
        mesh = mesh_from_meshdata(f"{node.get('type', 'node')} {node_id[-4:]}"[:63], meshdata)
        ctx.track(mesh)
        if mesh_key:
            cache[mesh_key] = mesh
    obj = bpy.data.objects.new(display_name(node), mesh)
    return obj


def _place_and_finish(ctx: BuildContext, node_id: str, node: Dict[str, Any], obj, params=None):
    parent_id = node.get("parentId")
    origin = ctx.level_origins.get(parent_id) if isinstance(parent_id, str) else None
    if origin is not None:
        obj.parent = origin
    anchor = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
    coll = anchor if isinstance(anchor, bpy.types.Collection) else ctx.import_collection
    link_object(ctx, obj, coll)
    stamp_datalayer(obj, ctx.verbatim_node(node_id), params, node_id in ctx.migrated_ids)
    if obj.data is not None:
        obj.data["pascal_id"] = node_id
    if node.get("visible") is False:
        obj.hide_viewport = True
        obj.hide_render = True
    ctx.anchors[node_id] = obj


def _solid_material(ctx: BuildContext, hex_color: str, roughness: float = 0.8):
    return get_material(ctx, None, "", resolved={
        "color": hex_color, "roughness": roughness, "metalness": 0.0,
        "opacity": 1.0, "transparent": False, "side": "front",
    })


def build_column(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    """Structural column: cylinder when radius is set, else width x depth box."""
    import bmesh

    height = float(schema.get(node, "height", 2.5))
    radius = node.get("radius")
    mesh = bpy.data.meshes.new(f"column {node_id[-4:]}")
    bm = bmesh.new()
    if radius:
        bmesh.ops.create_cone(
            bm, cap_ends=True, segments=24,
            radius1=float(radius), radius2=float(radius), depth=height,
        )
        bmesh.ops.translate(bm, vec=(0, 0, height / 2), verts=bm.verts[:])
    else:
        w = float(schema.get(node, "width", 0.2))
        d = float(schema.get(node, "depth", 0.2))
        bmesh.ops.create_cube(bm, size=1.0)
        bmesh.ops.scale(bm, vec=(w, d, height), verts=bm.verts[:])
        bmesh.ops.translate(bm, vec=(0, 0, height / 2), verts=bm.verts[:])
    bm.to_mesh(mesh)
    bm.free()
    ctx.track(mesh)

    obj = bpy.data.objects.new(display_name(node), mesh)
    obj.data.materials.append(get_material(ctx, node.get("material"), "wall"))
    obj.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    rotation = schema.get(node, "rotation", 0)
    if isinstance(rotation, (int, float)):
        obj.rotation_euler = (0, 0, coords.yrot_to_blender(float(rotation)))
    else:
        obj.rotation_euler = coords.euler_to_blender(rotation)
    _place_and_finish(ctx, node_id, node, obj, {"height": height})


def build_fence(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    boxes = plugin_geometry.fence_parts(node)
    if not boxes:
        ctx.note("WARNING", f"fence {node_id}: degenerate centerline")
        return
    if node.get("path") or node.get("curveOffset"):
        ctx.note("INFO", f"fence {node_id}: curved centerline approximated by straight chord")

    obj = _make_object(ctx, node_id, node, _boxes_to_meshdata(boxes))
    color = str(schema.get(node, "color", "#ffffff"))
    # slots: 0 posts, 1 infill, 2 base, 3 rail — same fence color, roughness varied
    for rough in (0.7, 0.75, 0.8, 0.65):
        obj.data.materials.append(_solid_material(ctx, color, rough))

    start = node["start"]
    sx, sz = float(start[0]), float(start[1])
    end = node["end"]
    support = float(schema.get(node, "supportOffset", 0.0))
    obj.location = (sx, -sz, support)
    obj.rotation_euler = (0.0, 0.0, coords.wall_angle_blender((sx, sz), (float(end[0]), float(end[1]))))
    _place_and_finish(ctx, node_id, node, obj, {
        "style": str(schema.get(node, "style", "slat")),
        "height": float(schema.get(node, "height", 1.8)),
    })


def build_shelf(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    boxes = plugin_geometry.shelf_parts(node)
    obj = _make_object(ctx, node_id, node, _boxes_to_meshdata(boxes))
    # slots: 0 shelves, 1 frame, 2 back — wood-ish defaults
    for hexc, rough in (("#deb887", 0.7), ("#b09468", 0.75), ("#c9b18a", 0.8)):
        obj.data.materials.append(_solid_material(ctx, hexc, rough))
    obj.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    obj.rotation_euler = coords.euler_to_blender(schema.get(node, "rotation", [0, 0, 0]))
    _place_and_finish(ctx, node_id, node, obj, {
        "style": str(schema.get(node, "style", "wall-shelf")),
        "width": float(schema.get(node, "width", 1.2)),
    })


def build_spawn(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    """Walkthrough start marker: Empty with arrow display (editor excludes
    spawn from exports too — no render geometry)."""
    empty = bpy.data.objects.new(display_name(node), None)
    empty.empty_display_type = "SINGLE_ARROW"
    empty.empty_display_size = 0.8
    empty.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    empty.rotation_euler = (0.0, 0.0, coords.yrot_to_blender(float(schema.get(node, "rotation", 0))))
    parent_id = node.get("parentId")
    origin = ctx.level_origins.get(parent_id) if isinstance(parent_id, str) else None
    if origin is not None:
        empty.parent = origin
    anchor = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
    coll = anchor if isinstance(anchor, bpy.types.Collection) else ctx.import_collection
    link_object(ctx, empty, coll)
    stamp_datalayer(empty, ctx.verbatim_node(node_id), None, node_id in ctx.migrated_ids)
    ctx.anchors[node_id] = empty


def build_tree(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    meshdata = _pascal_meshdata_to_blender(plugin_geometry.tree_proxy(node))
    bark, leaf = plugin_geometry.tree_colors(node)
    key = ("tree", str(node.get("preset")), str(node.get("size")), str(node.get("treeType")),
           node.get("seed"), node.get("height"), bark, leaf,
           bool(node.get("leafless")), node.get("trunkThickness"))
    obj = _make_object(ctx, node_id, node, meshdata, mesh_key=key)
    if len(obj.data.materials) == 0:
        obj.data.materials.append(_solid_material(ctx, bark, 0.9))   # 0 trunk
        obj.data.materials.append(_solid_material(ctx, leaf, 0.85))  # 1 canopy
    obj.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    obj.rotation_euler = coords.euler_to_blender(schema.get(node, "rotation", [0, 0, 0]))
    _place_and_finish(ctx, node_id, node, obj, {
        "preset": str(schema.get(node, "preset", "oak")),
        "height": float(schema.get(node, "height", 7)),
        "proxy": True,
    })


def build_grass(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    meshdata = _pascal_meshdata_to_blender(plugin_geometry.grass_tuft(node))
    color = str(schema.get(node, "bladeColor", "#5a8f3c"))
    key = ("grass", str(node.get("preset")), node.get("seed"), node.get("height"), color)
    obj = _make_object(ctx, node_id, node, meshdata, mesh_key=key)
    if len(obj.data.materials) == 0:
        obj.data.materials.append(_solid_material(ctx, color, 0.9))
    obj.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    obj.rotation_euler = coords.euler_to_blender(schema.get(node, "rotation", [0, 0, 0]))
    _place_and_finish(ctx, node_id, node, obj, {
        "preset": str(schema.get(node, "preset", "meadow")),
        "seed": int(schema.get(node, "seed", 1)),
    })
