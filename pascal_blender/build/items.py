"""Item placement: anchor Empty with the VERBATIM node transform; runtime-
derived offsets (slab elevation, wall-side push) go on the instance child so
export stays a pure read of the anchor (design §6.4).
"""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

import bpy

from ..core import coords, graph, schema
from . import assets
from .common import BuildContext, display_name, link_object, stamp_datalayer
from .cutters import add_cutter_box, ensure_wall_cutter_collection


def build_item(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    asset = node.get("asset") or {}
    attach = asset.get("attachTo")
    position = schema.get(node, "position", [0, 0, 0])
    rotation = schema.get(node, "rotation", [0, 0, 0])
    item_scale = schema.get(node, "scale", [1, 1, 1])

    anchor = bpy.data.objects.new(display_name(node), None)
    anchor.empty_display_type = "PLAIN_AXES"
    anchor.empty_display_size = 0.25

    parent_id = node.get("parentId")
    parent_node = ctx.nodes.get(parent_id) if isinstance(parent_id, str) else None
    parent_anchor = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
    parent_is_wall = isinstance(parent_node, dict) and parent_node.get("type") == "wall"
    parent_is_item = isinstance(parent_node, dict) and parent_node.get("type") == "item"
    parent_is_ceiling = isinstance(parent_node, dict) and parent_node.get("type") == "ceiling"

    slab_push = 0.0
    side_push = 0.0

    if parent_is_wall and isinstance(parent_anchor, bpy.types.Object):
        # Wall-local: Pascal (x, y, z) -> Blender local (x, -z, y); y = item BOTTOM.
        anchor.parent = parent_anchor
        anchor.location = coords.wall_local_to_blender(position)
        anchor.rotation_euler = coords.euler_to_blender(rotation)
        if attach == "wall-side":
            thickness = float(schema.get(parent_node, "thickness", schema.DEFAULT_WALL_THICKNESS))
            sign = 1.0 if node.get("side") == "front" else -1.0
            side_push = -(thickness / 2.0) * sign  # Pascal +z front -> Blender -y
        coll = _collection_of(ctx, parent_anchor)
    elif parent_is_item and isinstance(parent_anchor, bpy.types.Object):
        # Surface placement: the persisted position[1] ALREADY equals
        # surface.height * parentScale[1] (spec 04 §4.4) — the verbatim
        # transform is the anchor transform, nothing to add.
        anchor.parent = parent_anchor
        anchor.location = coords.loc_to_blender(position)
        anchor.rotation_euler = coords.euler_to_blender(rotation)
        coll = _collection_of(ctx, parent_anchor)
    elif parent_is_ceiling and isinstance(parent_anchor, bpy.types.Object):
        # Persisted position already encodes [x, -itemHeight, z] (spec 04
        # §4.4) — the verbatim transform is the anchor transform.
        anchor.parent = parent_anchor
        anchor.location = coords.loc_to_blender(position)
        anchor.rotation_euler = coords.euler_to_blender(rotation)
        coll = _collection_of(ctx, parent_anchor)
    else:
        # Floor item under the level origin; slab elevation is runtime-derived.
        origin = _level_origin(ctx, node)
        if origin is not None:
            anchor.parent = origin
        anchor.location = coords.loc_to_blender(position)
        anchor.rotation_euler = coords.euler_to_blender(rotation)
        slab_push = _slab_elevation_for_item(ctx, node)
        anchor_coll = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
        coll = anchor_coll if isinstance(anchor_coll, bpy.types.Collection) else ctx.import_collection

    link_object(ctx, anchor, coll)

    params = {
        "attachTo": str(attach or "floor"),
        "asset_id": str(asset.get("id", "")),
        "asset_src": str(asset.get("src", "")),
    }
    stamp_datalayer(anchor, ctx.verbatim_node(node_id), params, node_id in ctx.migrated_ids)
    if node.get("visible") is False:
        anchor.hide_viewport = True
        anchor.hide_render = True
    ctx.anchors[node_id] = anchor

    # Instance child: corrective transform ⊗ item scale + runtime pushes.
    corrective_offset = coords.loc_to_blender(asset.get("offset") or [0, 0, 0])
    child_loc = (
        corrective_offset[0],
        corrective_offset[1] + side_push,
        corrective_offset[2] + slab_push,
    )
    asset_scale = asset.get("scale") or [1, 1, 1]
    combined_scale = [float(a) * float(b) for a, b in zip(asset_scale, item_scale)]
    inst = assets.instance_asset_under(
        ctx,
        anchor,
        str(asset.get("src", "")),
        str(asset.get("name") or asset.get("id") or node_id[-4:]),
        asset.get("dimensions") or [1, 1, 1],
        asset_key=str(asset.get("id") or asset.get("src") or node_id),
        corrective_offset=child_loc,
        corrective_rotation=coords.euler_to_blender(asset.get("rotation") or [0, 0, 0]),
        corrective_scale=coords.scale_to_blender(combined_scale),
    )
    inst["pascal_runtime_offsets"] = {"slab": slab_push, "side": side_push}

    # GLB 'cutout' meshes on wall-attached items -> a cutter box on the wall.
    if attach == "wall" and parent_is_wall and isinstance(parent_anchor, bpy.types.Object):
        dims = asset.get("dimensions") or [1, 1, 1]
        sx = float(dims[0]) * float(item_scale[0])
        sz = float(dims[1]) * (float(item_scale[1]) if len(item_scale) > 1 else 1.0)
        thickness = float(schema.get(parent_node, "thickness", schema.DEFAULT_WALL_THICKNESS))
        cutter = add_cutter_box(ctx, parent_anchor, anchor, sx, sz, thickness)
        # Item anchor y = BOTTOM: center the hole vertically on the item middle.
        cutter.location = (0.0, 0.0, sz / 2.0)

    _build_lights(ctx, node_id, node, anchor)


def _collection_of(ctx: BuildContext, obj: "bpy.types.Object") -> "bpy.types.Collection":
    return obj.users_collection[0] if obj.users_collection else ctx.import_collection


def _level_origin(ctx: BuildContext, node: Dict[str, Any]):
    from .collections import level_origin_for

    return level_origin_for(ctx, node)


def _slab_elevation_for_item(ctx: BuildContext, item: Dict[str, Any]) -> float:
    """Approximation of the editor's footprint-overlap test: sample the item
    center against same-level slabs."""
    parent_id = item.get("parentId")
    level = ctx.nodes.get(parent_id) if isinstance(parent_id, str) else None
    if not isinstance(level, dict) or level.get("type") != "level":
        return 0.0
    position = schema.get(item, "position", [0, 0, 0])
    pt = (float(position[0]), float(position[2]))
    elevation = 0.0
    for cid in graph.iter_children_ids(level):
        slab = ctx.nodes.get(cid)
        if not isinstance(slab, dict) or slab.get("type") != "slab":
            continue
        poly = slab.get("polygon") or []
        if len(poly) >= 3 and graph._point_in_polygon(pt, poly) and not any(
            len(h) >= 3 and graph._point_in_polygon(pt, h) for h in slab.get("holes") or []
        ):
            elevation = max(elevation, float(schema.get(slab, "elevation", schema.DEFAULT_SLAB_ELEVATION)))
    return max(elevation, 0.0)


def _build_lights(ctx: BuildContext, node_id: str, node: Dict[str, Any], anchor: "bpy.types.Object") -> None:
    interactive = (node.get("asset") or {}).get("interactive") or {}
    effects = interactive.get("effects") or []
    controls = interactive.get("controls") or []
    for i, effect in enumerate(effects):
        if not isinstance(effect, dict) or effect.get("kind") != "light":
            continue
        light_data = bpy.data.lights.new(f"light {node_id[-4:]}.{i}", type="POINT")
        ctx.track(light_data)

        from ..core.materials import hex_to_linear_rgb

        light_data.color = hex_to_linear_rgb(str(effect.get("color", "#ffffff")))
        distance = effect.get("distance")
        if distance:
            light_data.use_custom_distance = True
            light_data.cutoff_distance = float(distance)

        intensity_range = effect.get("intensityRange") or [0, 1]
        is_on = False
        t = 0.0
        for control in controls:
            if not isinstance(control, dict):
                continue
            if control.get("kind") == "toggle":
                is_on = bool(control.get("default", False))
                break
            if control.get("kind") == "slider":
                lo, hi = float(control.get("min", 0)), float(control.get("max", 1))
                val = float(control.get("default", lo))
                t = (val - lo) / (hi - lo) if hi > lo else 0.0
                is_on = True
                break
        lo, hi = float(intensity_range[0]), float(intensity_range[1])
        intensity = (lo + (hi - lo) * t) if is_on else lo
        light_data.energy = intensity * float(ctx.options.get("light_watts", 60.0))

        light_obj = bpy.data.objects.new(light_data.name, light_data)
        light_obj.parent = anchor
        # Editor adds effect.offset in un-rotated world axes; counter-rotate
        # so the world placement matches despite parenting.
        offset = coords.loc_to_blender(effect.get("offset") or [0, 0, 0])
        rot_z = anchor.rotation_euler.z
        cos_r, sin_r = math.cos(-rot_z), math.sin(-rot_z)
        light_obj.location = (
            offset[0] * cos_r - offset[1] * sin_r,
            offset[0] * sin_r + offset[1] * cos_r,
            offset[2],
        )
        link_object(ctx, light_obj, _collection_of(ctx, anchor))
