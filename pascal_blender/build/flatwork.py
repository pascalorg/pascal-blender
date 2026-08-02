"""Slabs, ceilings, zones, site, scans, guides (design §4.2-4.3, §4.6-4.8)."""
from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

import bpy

from ..core import coords, schema
from ..core.materials import hex_to_linear_rgb
from .common import BuildContext, display_name, get_material, link_object, mesh_from_polygon, stamp_datalayer


def outset_polygon(polygon: List[List[float]], amount: float) -> List[Tuple[float, float]]:
    """Exact editor slab outset (spec 05 §6): shoelace winding sign, per-edge
    normal offset, line-intersection vertices, parallel fallback.
    Operates in PASCAL plan coords [x, z]."""
    pts = [(float(p[0]), float(p[1])) for p in polygon]
    n = len(pts)
    if n < 3:
        return pts

    shoelace = sum(
        pts[i][0] * pts[(i + 1) % n][1] - pts[(i + 1) % n][0] * pts[i][1]
        for i in range(n)
    )
    s = 1.0 if shoelace >= 0 else -1.0

    offset_edges = []
    for i in range(n):
        ax, az = pts[i]
        bx, bz = pts[(i + 1) % n]
        dx, dz = bx - ax, bz - az
        length = math.hypot(dx, dz)
        if length < 1e-9:
            offset_edges.append((ax, az, dx, dz))
            continue
        nx, nz = s * dz / length * amount, s * (-dx) / length * amount
        offset_edges.append((ax + nx, az + nz, dx, dz))

    out: List[Tuple[float, float]] = []
    for i in range(n):
        ax, az, adx, adz = offset_edges[(i - 1) % n]
        bx, bz, bdx, bdz = offset_edges[i]
        denom = adx * bdz - adz * bdx
        if abs(denom) < 1e-9:
            out.append((ax + adx, az + adz))
        else:
            t = ((bx - ax) * bdz - (bz - az) * bdx) / denom
            out.append((ax + adx * t, az + adz * t))
    return out


def _plan_poly_to_blender(polygon) -> List[Tuple[float, float]]:
    return [coords.plan_to_blender(p) for p in polygon]


def _link_to_level(ctx: BuildContext, node: Dict[str, Any], obj: "bpy.types.Object", coll=None) -> None:
    parent_id = node.get("parentId")
    origin = ctx.level_origins.get(parent_id) if isinstance(parent_id, str) else None
    if origin is not None:
        obj.parent = origin
    target = coll
    if target is None:
        anchor = ctx.anchors.get(parent_id) if isinstance(parent_id, str) else None
        target = anchor if isinstance(anchor, bpy.types.Collection) else ctx.import_collection
    link_object(ctx, obj, target)


def _finish(ctx: BuildContext, node_id: str, node: Dict[str, Any], obj: "bpy.types.Object", params: Dict[str, Any]) -> None:
    stamp_datalayer(obj, ctx.verbatim_node(node_id), params, node_id in ctx.migrated_ids)
    if obj.data is not None:
        obj.data["pascal_id"] = node_id
    if node.get("visible") is False:
        obj.hide_viewport = True
        obj.hide_render = True
    ctx.anchors[node_id] = obj


def build_slab(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    polygon = node.get("polygon") or []
    if len(polygon) < 3:
        ctx.note("WARNING", f"slab {node_id}: polygon has <3 points")
        return
    elevation = float(schema.get(node, "elevation", schema.DEFAULT_SLAB_ELEVATION))
    outset = outset_polygon(polygon, schema.SLAB_OUTSET)
    holes = [_plan_poly_to_blender(h) for h in node.get("holes") or [] if len(h) >= 3]
    mesh = mesh_from_polygon(f"Slab {node_id[-4:]}", _plan_poly_to_blender(outset), holes, extrude=elevation)
    ctx.track(mesh)
    obj = bpy.data.objects.new(display_name(node), mesh)
    obj.data.materials.append(get_material(ctx, node.get("material"), "slab"))
    _link_to_level(ctx, node, obj)
    _finish(ctx, node_id, node, obj, {"elevation": elevation})


def build_ceiling(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    polygon = node.get("polygon") or []
    if len(polygon) < 3:
        ctx.note("WARNING", f"ceiling {node_id}: polygon has <3 points")
        return
    height = float(schema.get(node, "height", schema.DEFAULT_CEILING_HEIGHT))
    holes = [_plan_poly_to_blender(h) for h in node.get("holes") or [] if len(h) >= 3]
    mesh = mesh_from_polygon(f"Ceiling {node_id[-4:]}", _plan_poly_to_blender(polygon), holes)
    ctx.track(mesh)
    obj = bpy.data.objects.new(display_name(node), mesh)
    obj.location = (0.0, 0.0, height - schema.CEILING_Z_FIGHT_OFFSET)
    mat = get_material(ctx, node.get("material"), "ceiling")
    mat.use_backface_culling = False  # visible from below
    obj.data.materials.append(mat)
    _link_to_level(ctx, node, obj)
    _finish(ctx, node_id, node, obj, {"height": height})


def build_zone(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    polygon = node.get("polygon") or []
    if len(polygon) < 3:
        return
    mesh = mesh_from_polygon(f"Zone {node_id[-4:]}", _plan_poly_to_blender(polygon))
    ctx.track(mesh)
    obj = bpy.data.objects.new(display_name(node), mesh)
    obj.location = (0.0, 0.0, 0.01)
    parent_id = node.get("parentId")
    origin = ctx.level_origins.get(parent_id) if isinstance(parent_id, str) else None
    if origin is not None:
        obj.parent = origin

    color = str(schema.get(node, "color", schema.DEFAULT_ZONE_COLOR))
    mat = bpy.data.materials.new(f"Pascal/zone-{color.lstrip('#')}")
    mat.use_nodes = True
    bsdf = next(n for n in mat.node_tree.nodes if n.type == "BSDF_PRINCIPLED")
    r, g, b = hex_to_linear_rgb(color)
    bsdf.inputs["Base Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Emission Color"].default_value = (r, g, b, 1.0)
    bsdf.inputs["Emission Strength"].default_value = 1.0
    bsdf.inputs["Alpha"].default_value = 0.25
    mat.surface_render_method = "BLENDED"
    ctx.track(mat)
    obj.data.materials.append(mat)

    link_object(ctx, obj, ctx.special["Zones"])
    _finish(ctx, node_id, node, obj, {"color": color})


def build_site_geometry(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    """Boundary curve + shadow-catcher plane; site contributes no solids."""
    poly_obj = node.get("polygon") or {}
    points = poly_obj.get("points") if isinstance(poly_obj, dict) else None
    if not points:
        points = schema.DEFAULT_SITE_POLYGON

    curve = bpy.data.curves.new(f"Site boundary {node_id[-4:]}", type="CURVE")
    curve.dimensions = "3D"
    spline = curve.splines.new("POLY")
    spline.points.add(len(points) - 1)
    for pt, target in zip(points, spline.points):
        x, y = coords.plan_to_blender(pt)
        target.co = (x, y, 0.01, 1.0)
    spline.use_cyclic_u = True
    ctx.track(curve)
    boundary = bpy.data.objects.new("Site boundary", curve)

    mesh = mesh_from_polygon(f"Site ground {node_id[-4:]}", _plan_poly_to_blender(points))
    ctx.track(mesh)
    ground = bpy.data.objects.new("Site ground", mesh)
    ground.is_shadow_catcher = True

    coll = ctx.anchors.get(node_id)
    target = coll if isinstance(coll, bpy.types.Collection) else ctx.import_collection
    link_object(ctx, boundary, target)
    link_object(ctx, ground, target)
    ground["pascal_site_ground_of"] = node_id
    boundary["pascal_site_boundary_of"] = node_id


def build_scan(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    from . import assets  # lazy: avoids import cost when unused

    empty = bpy.data.objects.new(display_name(node), None)
    empty.empty_display_type = "PLAIN_AXES"
    empty.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    empty.rotation_euler = coords.euler_to_blender(schema.get(node, "rotation", [0, 0, 0]))
    s = float(schema.get(node, "scale", 1))
    empty.scale = (s, s, s)
    _link_to_level(ctx, node, empty)
    _finish(ctx, node_id, node, empty, {"url": str(node.get("url", "")), "opacity": float(schema.get(node, "opacity", schema.DEFAULT_SCAN_OPACITY))})
    assets.instance_asset_under(ctx, empty, str(node.get("url", "")), f"scan {node_id[-4:]}", dimensions=[1, 1, 1])


def build_guide(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    scale = float(schema.get(node, "scale", 1))
    opacity = float(schema.get(node, "opacity", schema.DEFAULT_GUIDE_OPACITY))
    width = schema.GUIDE_BASE_WIDTH * scale
    height = width  # aspect unknowable for asset:// urls; square placeholder

    mesh = bpy.data.meshes.new(f"Guide {node_id[-4:]}")
    x, y = width / 2, height / 2
    mesh.from_pydata([(-x, -y, 0), (x, -y, 0), (x, y, 0), (-x, y, 0)], [], [(0, 1, 2, 3)])
    mesh.validate()
    ctx.track(mesh)
    obj = bpy.data.objects.new(display_name(node), mesh)
    obj.location = coords.loc_to_blender(schema.get(node, "position", [0, 0, 0]))
    rotation = schema.get(node, "rotation", [0, 0, 0])
    ry = rotation[1] if isinstance(rotation, (list, tuple)) and len(rotation) > 1 else 0.0
    obj.rotation_euler = (0.0, 0.0, coords.yrot_to_blender(ry))
    obj.display_type = "WIRE"

    url = str(node.get("url", ""))
    if url.startswith("asset://"):
        ctx.note("WARNING", f"guide {node_id}: url {url} is browser-local (IndexedDB); placeholder plane imported")
    _link_to_level(ctx, node, obj)
    _finish(ctx, node_id, node, obj, {"url": url, "opacity": opacity, "scale": scale})
