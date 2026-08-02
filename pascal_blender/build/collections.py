"""Collection hierarchy: Site -> Building -> Level as nested Collections,
level-origin Empties carrying the stacked Y offset, and the special
Zones / Cameras / Pascal Assets / Pascal Orphans / Pascal Unhandled bins.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import bpy

from ..core import graph, schema
from .common import BuildContext, display_name, link_object, new_collection, stamp_datalayer

SPECIAL_BINS = ("Zones", "Cameras", "Pascal Assets", "Pascal Orphans", "Pascal Unhandled")


def _layer_collection(root: "bpy.types.LayerCollection", coll: "bpy.types.Collection"):
    if root.collection == coll:
        return root
    for child in root.children:
        found = _layer_collection(child, coll)
        if found:
            return found
    return None


def exclude_collection(coll: "bpy.types.Collection") -> None:
    lc = _layer_collection(bpy.context.view_layer.layer_collection, coll)
    if lc:
        lc.exclude = True


def build_hierarchy(ctx: BuildContext, scene_name: str) -> None:
    scene_root = bpy.context.scene.collection
    top = new_collection(ctx, f"Pascal: {scene_name}"[:63], scene_root)
    ctx.import_collection = top
    top["pascal_source_hash"] = ctx.scene_data.sha256

    for bin_name in SPECIAL_BINS:
        ctx.special[bin_name] = new_collection(ctx, bin_name, top)

    # Container tree from rootNodeIds via children arrays.
    for root_id in ctx.scene_data.root_ids:
        node = ctx.nodes.get(root_id)
        if isinstance(node, dict):
            _build_container(ctx, root_id, node, top)

    # Orphans: anchors only, excluded from the view layer (invisible, like
    # the editor which never renders unreachable nodes).
    for orphan_id in sorted(graph.orphan_ids(ctx.nodes, ctx.scene_data.root_ids)):
        if orphan_id in ctx.anchors:
            continue
        node = ctx.nodes[orphan_id]
        if not isinstance(node, dict):
            continue
        empty = bpy.data.objects.new(display_name(node), None)
        empty.empty_display_size = 0.2
        stamp_datalayer(empty, ctx.verbatim_node(orphan_id), migrated=orphan_id in ctx.migrated_ids)
        link_object(ctx, empty, ctx.special["Pascal Orphans"])
        ctx.anchors[orphan_id] = empty
    ctx.note("INFO", f"{len(graph.orphan_ids(ctx.nodes, ctx.scene_data.root_ids))} orphan node(s) preserved in Pascal Orphans")

    for bin_name in ("Zones", "Pascal Orphans", "Pascal Unhandled"):
        exclude_collection(ctx.special[bin_name])
    ctx.special["Pascal Assets"].hide_viewport = True
    ctx.special["Pascal Assets"].hide_render = True


def _build_container(
    ctx: BuildContext,
    node_id: str,
    node: Dict[str, Any],
    parent_coll: "bpy.types.Collection",
) -> None:
    ntype = node.get("type")
    if ntype not in ("site", "building", "level"):
        return  # non-container roots handled by the node builders later

    coll = new_collection(ctx, display_name(node), parent_coll)
    stamp_datalayer(coll, ctx.verbatim_node(node_id), migrated=node_id in ctx.migrated_ids)
    ctx.anchors[node_id] = coll
    if node.get("visible") is False:
        exclude_collection(coll)

    if ntype == "site" and graph.embedded_children(node):
        coll["pascal_site_children_embedded"] = True
        # Hoist embedded node objects into the working node dict so builders
        # treat them uniformly; verbatim form survives in pascal_json.
        for child in graph.embedded_children(node):
            cid = str(child.get("id"))
            if cid and cid not in ctx.nodes:
                ctx.nodes[cid] = child

    if ntype == "building":
        ctx.level_offsets.update(graph.level_offsets(node, ctx.nodes))

    if ntype == "level":
        offset = ctx.level_offsets.get(node_id, 0.0)
        origin = bpy.data.objects.new(f"{display_name(node)} Origin"[:63], None)
        origin.empty_display_type = "PLAIN_AXES"
        origin.empty_display_size = 0.5
        origin.location = (0.0, 0.0, offset)
        origin["pascal_level_origin_of"] = node_id
        link_object(ctx, origin, coll)
        ctx.level_origins[node_id] = origin

    for child_id in graph.iter_children_ids(node):
        child = ctx.nodes.get(child_id)
        if isinstance(child, dict) and child.get("type") in ("site", "building", "level"):
            _build_container(ctx, child_id, child, coll)


def collection_for_node(ctx: BuildContext, node: Dict[str, Any]) -> "bpy.types.Collection":
    """The collection a built object should be linked into: its level's
    collection when it has one, else the import root."""
    parent_id = node.get("parentId")
    anchor = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
    if isinstance(anchor, bpy.types.Collection):
        return anchor
    return ctx.import_collection


def level_origin_for(ctx: BuildContext, node: Dict[str, Any]) -> Optional["bpy.types.Object"]:
    """Walk up parentId until a level with an origin Empty is found."""
    seen = set()
    current = node
    while isinstance(current, dict):
        pid = current.get("parentId")
        if not isinstance(pid, str) or pid in seen:
            return None
        seen.add(pid)
        if pid in ctx.level_origins:
            return ctx.level_origins[pid]
        current = ctx.nodes.get(pid)
    return None
