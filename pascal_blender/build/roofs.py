"""Roof container Empties + roof-segment mesh objects (design §4.4).

Segment geometry comes from build/roofs_geometry.py (spec 03); this module
handles placement, materials (4-slot scheme), and the data layer.
"""
from __future__ import annotations

from typing import Any, Dict

import bpy

from ..core import coords, graph, schema
from ..core.materials import ROOF_SLOT_MATERIALS
from .common import BuildContext, display_name, get_material, link_object, stamp_datalayer
from .roofs_geometry import build_roof_segment_mesh


def build_roof(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    empty = bpy.data.objects.new(display_name(node), None)
    empty.empty_display_type = "PLAIN_AXES"
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
    if node.get("visible") is False:
        empty.hide_viewport = True
        empty.hide_render = True
    ctx.anchors[node_id] = empty

    for child_id in graph.iter_children_ids(node):
        child = ctx.nodes.get(child_id)
        if isinstance(child, dict) and child.get("type") == "roof-segment":
            build_roof_segment(ctx, child_id, child, empty, node, coll)


def build_roof_segment(
    ctx: BuildContext,
    node_id: str,
    node: Dict[str, Any],
    roof_empty: "bpy.types.Object",
    roof_node: Dict[str, Any],
    coll: "bpy.types.Collection",
) -> None:
    mesh = build_roof_segment_mesh(node, f"rseg {node_id[-4:]}")
    ctx.track(mesh)
    obj = bpy.data.objects.new(display_name(node), mesh)
    obj.parent = roof_empty
    obj.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    obj.rotation_euler = (0.0, 0.0, coords.yrot_to_blender(float(schema.get(node, "rotation", 0))))

    material = node.get("material") or roof_node.get("material")
    if material:
        # A segment with its own material gets a single resolved slot (parity).
        obj.data.materials.append(get_material(ctx, material, "roof"))
        for poly in mesh.polygons:
            poly.material_index = 0
    else:
        for slot in ROOF_SLOT_MATERIALS:
            obj.data.materials.append(get_material(ctx, None, "", resolved=dict(slot)))

    params = {
        key: schema.get(node, key, default)
        for key, default in schema.ROOF_SEGMENT_DEFAULTS.items()
        if not isinstance(default, str)
    }
    params["roofType"] = str(schema.get(node, "roofType", "gable"))
    stamp_datalayer(obj, ctx.verbatim_node(node_id), params, node_id in ctx.migrated_ids)
    mesh["pascal_id"] = node_id
    if node.get("visible") is False:
        obj.hide_viewport = True
        obj.hide_render = True
    link_object(ctx, obj, coll)
    ctx.anchors[node_id] = obj
