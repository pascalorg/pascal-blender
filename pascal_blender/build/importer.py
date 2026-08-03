"""Import orchestration: parse -> migrate -> hierarchy -> per-type builders
-> cutter booleans -> report. Transaction-ish: on exception every datablock
created so far is removed so no partial import is left behind.
"""
from __future__ import annotations

import os
from typing import Any, Dict, Optional

import bpy

from ..core import graph, migrations, parse, schema
from . import cameras, collections as build_collections, cutters, datalayer, flatwork, items, openings_build, plugins, roofs, walls
from .common import BuildContext, display_name, link_object, stamp_datalayer

DEFAULT_OPTIONS: Dict[str, Any] = {
    "bake_openings": False,
    "item_materials": "editor",
    "physical_glass": False,
    "apply_texture_field": True,
    "make_instances_real": False,
    "network": True,
    "cdn_base": "https://editor.pascal.app",
    "light_watts": 60.0,
}


def import_scene(filepath: str, options: Optional[Dict[str, Any]] = None) -> BuildContext:
    opts = {**DEFAULT_OPTIONS, **(options or {})}
    scene_data = parse.load_scene(filepath)
    migrated_nodes, migrated_ids = migrations.migrate_nodes(scene_data.nodes)

    ctx = BuildContext(
        scene_data=scene_data,
        nodes=dict(migrated_nodes),
        migrated_ids=migrated_ids,
        options=opts,
    )
    try:
        _build(ctx, os.path.basename(filepath))
    except Exception:
        _rollback(ctx)
        raise
    return ctx


def _build(ctx: BuildContext, scene_name: str) -> None:
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 1.0
    scene.unit_settings.length_unit = "METERS"

    build_collections.build_hierarchy(ctx, scene_name)
    datalayer.create_scene_datalayer(ctx)

    # Walls first (per level, so miters resolve and openings can parent).
    for level_id, level in _typed(ctx, "level"):
        walls.build_walls_for_level(ctx, level_id, level)

    builders = {
        "slab": flatwork.build_slab,
        "ceiling": flatwork.build_ceiling,
        "zone": flatwork.build_zone,
        "scan": flatwork.build_scan,
        "guide": flatwork.build_guide,
        "roof": roofs.build_roof,
        "door": openings_build.build_door,
        "window": openings_build.build_window,
        "item": items.build_item,
        "fence": plugins.build_fence,
        "shelf": plugins.build_shelf,
        "spawn": plugins.build_spawn,
        "trees:tree": plugins.build_tree,
        "trees:grass": plugins.build_grass,
    }
    order = [
        "slab", "ceiling", "zone", "scan", "guide", "roof", "door", "window",
        "item", "fence", "shelf", "spawn", "trees:tree", "trees:grass",
    ]
    reachable = graph.reachable_ids(ctx.nodes, ctx.scene_data.root_ids)
    for ntype in order:
        for node_id, node in _typed(ctx, ntype):
            if node_id not in reachable or node_id in ctx.anchors:
                continue
            try:
                builders[ntype](ctx, node_id, node)
            except Exception as exc:  # noqa: BLE001 — one bad node never kills the import
                ctx.note("ERROR", f"{ntype} {node_id}: builder failed: {exc}")
                _fallback_anchor(ctx, node_id, node)

    # Anything reachable but unbuilt (unknown types, site geometry side-cars).
    for node_id in sorted(reachable):
        node = ctx.nodes.get(node_id)
        if not isinstance(node, dict) or node_id in ctx.anchors:
            if node_id in ctx.anchors and node.get("type") == "site":
                flatwork.build_site_geometry(ctx, node_id, node)
            continue
        ntype = node.get("type")
        if ntype not in schema.KNOWN_NODE_TYPES:
            ctx.note("WARNING", f"unknown node type {ntype!r} ({node_id}) preserved in Pascal Unhandled")
        _fallback_anchor(ctx, node_id, node, unhandled=True)

    for wall_obj in [o for o in ctx.anchors.values() if isinstance(o, bpy.types.Object) and o.get("pascal_type") == "wall" and o.type == "MESH"]:
        cutters.wire_wall_boolean(ctx, wall_obj)

    for node_id, node in ctx.nodes.items():
        if isinstance(node, dict) and isinstance(node.get("camera"), dict):
            cameras.build_camera(ctx, node_id, node)

    missing = [nid for nid in ctx.scene_data.nodes if nid not in ctx.anchors]
    for nid in missing:
        node = ctx.scene_data.nodes.get(nid)
        if isinstance(node, dict):
            _fallback_anchor(ctx, nid, node, unhandled=True)

    # Nodes synthesized by migrations (e.g. legacy-roof segments) have no
    # existence in the source file — flag them so the exporter never re-emits
    # them as independent nodes (design §8.5).
    for node_id, anchor in ctx.anchors.items():
        if node_id not in ctx.scene_data.nodes:
            anchor["pascal_synthetic"] = True

    scene["pascal_import_report"] = "\n".join(f"[{lvl}] {msg}" for lvl, msg in ctx.report) or "clean import"


def _typed(ctx: BuildContext, ntype: str):
    return [
        (nid, n) for nid, n in ctx.nodes.items()
        if isinstance(n, dict) and n.get("type") == ntype
    ]


def _fallback_anchor(ctx: BuildContext, node_id: str, node: Dict[str, Any], unhandled: bool = False) -> None:
    if node_id in ctx.anchors:
        return
    empty = bpy.data.objects.new(display_name(node), None)
    empty.empty_display_size = 0.2
    stamp_datalayer(empty, ctx.verbatim_node(node_id), migrated=node_id in ctx.migrated_ids)
    target = ctx.special.get("Pascal Unhandled") if unhandled else ctx.import_collection
    link_object(ctx, empty, target or ctx.import_collection)
    ctx.anchors[node_id] = empty


def _rollback(ctx: BuildContext) -> None:
    for datablock in reversed(ctx.created_ids):
        try:
            if isinstance(datablock, bpy.types.Object):
                bpy.data.objects.remove(datablock, do_unlink=True)
            elif isinstance(datablock, bpy.types.Collection):
                bpy.data.collections.remove(datablock)
            elif isinstance(datablock, bpy.types.Mesh):
                bpy.data.meshes.remove(datablock)
            elif isinstance(datablock, bpy.types.Material):
                bpy.data.materials.remove(datablock)
            elif isinstance(datablock, bpy.types.Text):
                bpy.data.texts.remove(datablock)
            elif isinstance(datablock, bpy.types.Light):
                bpy.data.lights.remove(datablock)
            elif isinstance(datablock, bpy.types.Camera):
                bpy.data.cameras.remove(datablock)
        except (ReferenceError, RuntimeError):
            pass
