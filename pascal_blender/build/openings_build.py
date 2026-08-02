"""Parametric door/window objects: mesh from core.openings, parented to the
wall, hidden cutter box registered on the wall (design §4.5)."""
from __future__ import annotations

from typing import Any, Dict

import bpy

from ..core import coords, schema
from ..core import openings as core_openings
from ..core.materials import OPENING_BASE, OPENING_GLASS
from .common import BuildContext, display_name, get_material, link_object, mesh_from_meshdata, stamp_datalayer
from .cutters import add_cutter_box


def _build_opening(ctx: BuildContext, node_id: str, node: Dict[str, Any], kind: str) -> None:
    if kind == "door":
        meshdata = core_openings.build_door_geometry(node)
        cut_w, cut_h = core_openings.door_cutout_size(node)
    else:
        meshdata = core_openings.build_window_geometry(node)
        cut_w, cut_h = core_openings.window_cutout_size(node)

    mesh = mesh_from_meshdata(f"{kind} {node_id[-4:]}", meshdata)
    ctx.track(mesh)
    obj = bpy.data.objects.new(display_name(node), mesh)

    obj.data.materials.append(get_material(ctx, None, "", resolved=dict(OPENING_BASE)))
    obj.data.materials.append(get_material(ctx, None, "", resolved=dict(OPENING_GLASS)))

    parent_id = node.get("parentId") or node.get("wallId")
    parent_anchor = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
    parent_node = ctx.nodes.get(parent_id) if isinstance(parent_id, str) else None
    wall_thickness = schema.DEFAULT_WALL_THICKNESS
    if isinstance(parent_node, dict) and parent_node.get("type") == "wall":
        wall_thickness = float(schema.get(parent_node, "thickness", schema.DEFAULT_WALL_THICKNESS))

    position = schema.get(node, "position", [0, 0, 0])
    rotation = schema.get(node, "rotation", [0, 0, 0])
    if isinstance(parent_anchor, bpy.types.Object):
        obj.parent = parent_anchor
        obj.location = coords.wall_local_to_blender(position)
        ry = rotation[1] if isinstance(rotation, (list, tuple)) and len(rotation) > 1 else 0.0
        obj.rotation_euler = (0.0, 0.0, coords.yrot_to_blender(ry))
        coll = parent_anchor.users_collection[0] if parent_anchor.users_collection else ctx.import_collection
    else:
        ctx.note("WARNING", f"{kind} {node_id}: wall {parent_id!r} not found; placed unparented")
        obj.location = coords.wall_local_to_blender(position)
        coll = ctx.import_collection
    link_object(ctx, obj, coll)

    stamp_datalayer(obj, ctx.verbatim_node(node_id), {"width": cut_w, "height": cut_h}, node_id in ctx.migrated_ids)
    mesh["pascal_id"] = node_id
    if node.get("visible") is False:
        obj.hide_viewport = True
        obj.hide_render = True
    ctx.anchors[node_id] = obj

    if isinstance(parent_anchor, bpy.types.Object) and parent_anchor.type == "MESH":
        add_cutter_box(ctx, parent_anchor, obj, cut_w, cut_h, wall_thickness)


def build_door(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    _build_opening(ctx, node_id, node, "door")


def build_window(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    _build_opening(ctx, node_id, node, "window")
