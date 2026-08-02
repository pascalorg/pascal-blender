"""GLB asset pipeline: URL resolution, cached download, one import per asset,
collection instancing, light effects (design §6)."""
from __future__ import annotations

import hashlib
import os
import urllib.request
from typing import Any, Dict, List, Optional

import bpy

from .common import BuildContext, link_object

DEFAULT_CDN = "https://editor.pascal.app"


def resolve_url(ctx: BuildContext, url: str) -> Optional[str]:
    if not url:
        return None
    if url.startswith(("http://", "https://")):
        return url
    if url.startswith("asset://"):
        return None  # browser IndexedDB blob — unrecoverable
    base = str(ctx.options.get("cdn_base") or DEFAULT_CDN).rstrip("/")
    return f"{base}/{url.lstrip('/')}"


def _cache_dir(ctx: BuildContext) -> str:
    configured = ctx.options.get("cache_dir")
    if configured:
        os.makedirs(configured, exist_ok=True)
        return configured
    try:
        return bpy.utils.extension_path_user(__package__.split(".")[0], path="cache", create=True)
    except Exception:
        import tempfile

        path = os.path.join(tempfile.gettempdir(), "pascal_blender_cache")
        os.makedirs(path, exist_ok=True)
        return path


def _download(ctx: BuildContext, url: str) -> Optional[str]:
    if not ctx.options.get("network", True):
        return None
    if hasattr(bpy.app, "online_access") and not bpy.app.online_access:
        ctx.note("WARNING", f"online access disabled in Blender preferences; placeholder for {url}")
        return None
    path = os.path.join(_cache_dir(ctx), hashlib.sha1(url.encode()).hexdigest() + ".glb")
    if os.path.exists(path):
        return path
    try:
        with urllib.request.urlopen(url, timeout=30) as response, open(path, "wb") as out:
            out.write(response.read())
        return path
    except Exception as exc:  # noqa: BLE001 — any fetch failure -> placeholder
        ctx.note("WARNING", f"download failed for {url}: {exc}")
        if os.path.exists(path):
            os.remove(path)
        return None


def _placeholder_collection(ctx: BuildContext, key: str, label: str, dimensions: List[float]) -> "bpy.types.Collection":
    coll = bpy.data.collections.new(f"Asset {label}"[:63])
    ctx.special["Pascal Assets"].children.link(coll)
    ctx.track(coll)
    w = float(dimensions[0]) if len(dimensions) > 0 else 1.0
    h = float(dimensions[1]) if len(dimensions) > 1 else 1.0
    d = float(dimensions[2]) if len(dimensions) > 2 else 1.0
    # Pascal dimensions [w, h, d] -> Blender (x=w, y=d, z=h); box sits on floor.
    mesh = bpy.data.meshes.new(f"placeholder {label}"[:63])
    x, y = w / 2, d / 2
    from .cutters import BOX_FACES

    verts = [
        (-x, -y, 0), (x, -y, 0), (x, y, 0), (-x, y, 0),
        (-x, -y, h), (x, -y, h), (x, y, h), (-x, y, h),
    ]
    mesh.from_pydata(verts, [], BOX_FACES)
    mesh.validate()
    ctx.track(mesh)
    obj = bpy.data.objects.new(f"placeholder {label}"[:63], mesh)
    obj.display_type = "WIRE"
    obj["pascal_placeholder"] = True
    link_object(ctx, obj, coll)
    return coll


def get_asset_collection(
    ctx: BuildContext,
    asset_key: str,
    url: str,
    label: str,
    dimensions: List[float],
) -> "bpy.types.Collection":
    """One collection per distinct asset; GLB imported once, instanced N times."""
    cache: Dict[str, Any] = ctx.options.setdefault("_asset_collections", {})
    if asset_key in cache:
        return cache[asset_key]

    resolved = resolve_url(ctx, url)
    path = _download(ctx, resolved) if resolved else None
    if path is None:
        coll = _placeholder_collection(ctx, asset_key, label, dimensions)
        coll["pascal_asset_url"] = url
        cache[asset_key] = coll
        return coll

    coll = bpy.data.collections.new(f"Asset {label}"[:63])
    ctx.special["Pascal Assets"].children.link(coll)
    ctx.track(coll)
    coll["pascal_asset_url"] = url

    before = set(bpy.data.objects)
    try:
        bpy.ops.import_scene.gltf(filepath=path)
    except Exception as exc:  # noqa: BLE001
        ctx.note("WARNING", f"glTF import failed for {url}: {exc}")
        cache[asset_key] = _placeholder_collection(ctx, asset_key, label, dimensions)
        return cache[asset_key]

    imported = [o for o in bpy.data.objects if o not in before]
    for obj in imported:
        for user_coll in list(obj.users_collection):
            user_coll.objects.unlink(obj)
        coll.objects.link(obj)
        ctx.track(obj)
        if obj.name.lower().startswith("cutout") or (obj.data and obj.data.name.lower().startswith("cutout")):
            obj.hide_viewport = True
            obj.hide_render = True
            obj["pascal_glb_cutout"] = True
    cache[asset_key] = coll
    return coll


def instance_asset_under(
    ctx: BuildContext,
    parent: "bpy.types.Object",
    url: str,
    label: str,
    dimensions: List[float],
    asset_key: Optional[str] = None,
    corrective_offset=(0.0, 0.0, 0.0),
    corrective_rotation=(0.0, 0.0, 0.0),
    corrective_scale=(1.0, 1.0, 1.0),
) -> "bpy.types.Object":
    coll = get_asset_collection(ctx, asset_key or url, url, label, dimensions)
    inst = bpy.data.objects.new(f"{label} model"[:63], None)
    inst.instance_type = "COLLECTION"
    inst.instance_collection = coll
    inst.parent = parent
    inst.location = corrective_offset
    inst.rotation_euler = corrective_rotation
    inst.scale = corrective_scale
    target = parent.users_collection[0] if parent.users_collection else ctx.import_collection
    link_object(ctx, inst, target)
    return inst
