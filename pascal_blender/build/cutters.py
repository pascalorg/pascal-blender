"""Boolean cutter infrastructure for wall openings (design §4.1).

One Boolean modifier per wall with a COLLECTION operand pointing at that
wall's hidden cutter collection. Solver MANIFOLD; its failure mode is a
SILENT no-op, so after wiring we assert the evaluated volume actually
changed and fall back to EXACT (reported) if not.
"""
from __future__ import annotations

import bpy

from .common import BuildContext, link_object, mesh_volume, new_collection

CUTTER_EXTRA_DEPTH = 0.02  # cutter depth = wallThickness + 2 cm

# Outward-facing quads for the 8-vert box layout used below
# (bottom ring 0-3, top ring 4-7, CCW from above).
BOX_FACES = [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]


def _recalc_normals(mesh: "bpy.types.Mesh") -> None:
    import bmesh

    bm = bmesh.new()
    bm.from_mesh(mesh)
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    bm.to_mesh(mesh)
    bm.free()


def ensure_wall_cutter_collection(ctx: BuildContext, wall_obj: "bpy.types.Object") -> "bpy.types.Collection":
    wall_id = wall_obj.get("pascal_id", wall_obj.name)
    coll = ctx.cutter_collections.get(wall_id)
    if coll is None:
        coll = new_collection(ctx, f"Cutters - {wall_obj.name}"[:63], ctx.import_collection)
        coll.hide_viewport = True
        coll.hide_render = True
        ctx.cutter_collections[wall_id] = coll
    return coll


def add_cutter_box(
    ctx: BuildContext,
    wall_obj: "bpy.types.Object",
    opening_obj: "bpy.types.Object",
    width: float,
    height: float,
    wall_thickness: float,
) -> "bpy.types.Object":
    """A hidden box parented to the opening object so moving the opening
    moves its hole. Local frame matches the opening: X along wall,
    Y across the wall, Z up; the box is centered on the opening origin."""
    depth = wall_thickness + CUTTER_EXTRA_DEPTH
    mesh = bpy.data.meshes.new(f"cutter {opening_obj.name}"[:63])
    x, y, z = width / 2, depth / 2, height / 2
    verts = [
        (-x, -y, -z), (x, -y, -z), (x, y, -z), (-x, y, -z),
        (-x, -y, z), (x, -y, z), (x, y, z), (-x, y, z),
    ]
    mesh.from_pydata(verts, [], BOX_FACES)
    mesh.validate()
    _recalc_normals(mesh)
    ctx.track(mesh)

    cutter = bpy.data.objects.new(mesh.name, mesh)
    cutter.parent = opening_obj
    cutter.display_type = "WIRE"
    cutter.hide_viewport = True
    cutter.hide_render = True
    cutter["pascal_cutter_for"] = wall_obj.get("pascal_id", "")
    link_object(ctx, cutter, ensure_wall_cutter_collection(ctx, wall_obj))
    return cutter


def wire_wall_boolean(ctx: BuildContext, wall_obj: "bpy.types.Object") -> None:
    wall_id = wall_obj.get("pascal_id", wall_obj.name)
    coll = ctx.cutter_collections.get(wall_id)
    if coll is None or not coll.objects:
        return

    uncut = mesh_volume(wall_obj, evaluated=False)
    mod = wall_obj.modifiers.new("Pascal Openings", "BOOLEAN")
    mod.operation = "DIFFERENCE"
    mod.operand_type = "COLLECTION"
    mod.collection = coll
    mod.solver = "MANIFOLD"

    bpy.context.view_layer.update()
    if abs(mesh_volume(wall_obj) - uncut) < 1e-9:
        mod.solver = "EXACT"
        bpy.context.view_layer.update()
        if abs(mesh_volume(wall_obj) - uncut) < 1e-9:
            ctx.note("WARNING", f"boolean cut had no effect on {wall_obj.name}")
        else:
            ctx.note("INFO", f"{wall_obj.name}: MANIFOLD solver no-op, fell back to EXACT")

    if ctx.options.get("bake_openings"):
        depsgraph = bpy.context.evaluated_depsgraph_get()
        baked = bpy.data.meshes.new_from_object(
            wall_obj.evaluated_get(depsgraph), depsgraph=depsgraph
        )
        old = wall_obj.data
        wall_obj.modifiers.remove(mod)
        wall_obj.data = baked
        ctx.track(baked)
        if old.users == 0:
            bpy.data.meshes.remove(old)
