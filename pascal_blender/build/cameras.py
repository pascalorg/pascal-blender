"""Per-node saved cameras -> Camera objects in the Cameras collection.

Camera coordinates are WORLD coords captured live in the editor (level offset
already included) — never re-add level Y. Rotation is a computed look-at
(no constraint, keeps the object self-contained).
"""
from __future__ import annotations

import math
from typing import Any, Dict

import bpy
from mathutils import Vector

from ..core import coords
from .common import BuildContext, link_object

DEFAULT_PERSP_FOV_DEG = 50.0
DEFAULT_ORTHO_ZOOM = 20.0


def build_camera(ctx: BuildContext, node_id: str, node: Dict[str, Any]) -> None:
    cam = node.get("camera")
    if not isinstance(cam, dict) or "position" not in cam or "target" not in cam:
        return

    data = bpy.data.cameras.new(f"cam {node_id[-4:]}")
    ctx.track(data)
    mode = cam.get("mode", "perspective")
    if mode == "orthographic":
        data.type = "ORTHO"
        zoom = float(cam.get("zoom") or DEFAULT_ORTHO_ZOOM)
        data.ortho_scale = 100.0 / max(zoom, 1e-6)
    else:
        data.type = "PERSP"
        data.angle = math.radians(float(cam.get("fov") or DEFAULT_PERSP_FOV_DEG))

    obj = bpy.data.objects.new(f"Camera {node_id[-4:]}", data)
    position = Vector(coords.loc_to_blender(cam["position"]))
    target = Vector(coords.loc_to_blender(cam["target"]))
    obj.location = position
    direction = target - position
    if direction.length > 1e-9:
        obj.rotation_euler = direction.to_track_quat("-Z", "Y").to_euler()

    obj["pascal_camera_of"] = node_id
    obj["pascal_camera_target"] = list(cam["target"])
    link_object(ctx, obj, ctx.special["Cameras"])
