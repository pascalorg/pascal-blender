"""Wall building: mitered footprint (core.wallnet) extruded to height,
placed at start with the wall angle, parented to the level origin.
"""
from __future__ import annotations

import math
from typing import Any, Dict

import bpy

from ..core import coords, graph, schema
from ..core import wallnet
from . import collections as build_collections
from .common import BuildContext, display_name, get_material, link_object, mesh_from_polygon, stamp_datalayer


def build_walls_for_level(ctx: BuildContext, level_id: str, level: Dict[str, Any]) -> None:
    """Build every wall of one level together so junctions miter correctly."""
    children = {
        cid: ctx.nodes[cid]
        for cid in graph.iter_children_ids(level)
        if cid in ctx.nodes and isinstance(ctx.nodes[cid], dict)
    }
    walls = {cid: n for cid, n in children.items() if n.get("type") == "wall"}
    if not walls:
        return

    footprints = wallnet.wall_footprints(walls)
    level_children = list(children.values())

    for wall_id, wall in walls.items():
        try:
            (sx, sz), (ex, ez) = schema.wall_start_end(wall)
        except (KeyError, IndexError, TypeError):
            ctx.note("WARNING", f"wall {wall_id} missing start/end; data-layer anchor only")
            _anchor_only(ctx, wall_id, wall)
            continue

        height = float(schema.get(wall, "height", schema.DEFAULT_WALL_HEIGHT))
        thickness = float(schema.get(wall, "thickness", schema.DEFAULT_WALL_THICKNESS))
        slab_elev = graph.slab_elevation_for_wall(wall, level_children)

        # Negative slab elevation stretches the wall downward (top stays put).
        z_base = 0.0
        extrude = height
        if slab_elev < 0:
            extrude = height - slab_elev
            z_base = 0.0  # base at the (negative) slab elevation via object Z

        # Footprint is in Pascal wall-local (x along wall, z across);
        # Blender ground plane local = (x, -z).
        footprint = footprints.get(wall_id)
        if not footprint:
            length = math.hypot(ex - sx, ez - sz)
            t2 = thickness / 2
            footprint = [(0, -t2), (length, -t2), (length, t2), (0, t2)]
        poly_2d = [coords.plan_to_blender(p) for p in footprint]

        mesh = mesh_from_polygon(f"Wall {wall_id[-4:]}", poly_2d, extrude=extrude)
        ctx.track(mesh)
        obj = bpy.data.objects.new(display_name(wall), mesh)
        obj.location = (sx, -sz, slab_elev if slab_elev >= 0 else slab_elev)
        obj.rotation_euler = (0.0, 0.0, coords.wall_angle_blender((sx, sz), (ex, ez)))

        origin = ctx.level_origins.get(level_id)
        if origin is not None:
            obj.parent = origin

        material = get_material(ctx, wall.get("material"), "wall")
        obj.data.materials.append(material)

        params = {
            "start": [sx, sz], "end": [ex, ez],
            "thickness": thickness, "height": height,
            "slab_elevation": slab_elev,
        }
        stamp_datalayer(obj, ctx.verbatim_node(wall_id), params, wall_id in ctx.migrated_ids)
        mesh["pascal_id"] = wall_id
        if wall.get("visible") is False:
            obj.hide_viewport = True
            obj.hide_render = True

        coll = ctx.anchors.get(level_id)
        link_object(ctx, obj, coll if isinstance(coll, bpy.types.Collection) else ctx.import_collection)
        ctx.anchors[wall_id] = obj


def _anchor_only(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    empty = bpy.data.objects.new(display_name(node), None)
    empty.empty_display_size = 0.2
    stamp_datalayer(empty, ctx.verbatim_node(node_id), migrated=node_id in ctx.migrated_ids)
    link_object(ctx, empty, ctx.special.get("Pascal Unhandled", ctx.import_collection))
    ctx.anchors[node_id] = empty
