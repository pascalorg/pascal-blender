"""Scene-level lossless data layer (design §2.2 tier 1).

The complete original file lives pretty-printed in a Text datablock — always
multi-line (single-line Text writes are quadratic in Blender), byte content
recoverable via the recorded sha256 of the ORIGINAL bytes.
"""
from __future__ import annotations

import json

import bpy

from .common import BuildContext

SOURCE_TEXT_NAME = "pascal_source.json"
SCHEMA_VERSION = "1"


def create_scene_datalayer(ctx: BuildContext) -> None:
    text = bpy.data.texts.new(SOURCE_TEXT_NAME)
    text.write(ctx.scene_data.raw_bytes.decode("utf-8"))
    text.use_fake_user = True
    ctx.track(text)

    scene = bpy.context.scene
    scene["pascal_schema_version"] = SCHEMA_VERSION
    scene["pascal_source_hash"] = ctx.scene_data.sha256
    scene["pascal_source_text"] = text.name
    scene["pascal_root_node_ids"] = list(ctx.scene_data.root_ids)
    if ctx.scene_data.extra_toplevel:
        scene["pascal_extra_toplevel_json"] = json.dumps(
            ctx.scene_data.extra_toplevel, ensure_ascii=False
        )
    if ctx.scene_data.filename:
        scene["pascal_source_file"] = ctx.scene_data.filename
