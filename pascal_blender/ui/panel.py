"""N-panel: Pascal node info for the active object + the import report."""
from __future__ import annotations

import bpy


class PASCAL_PT_node(bpy.types.Panel):
    bl_idname = "PASCAL_PT_node"
    bl_label = "Pascal"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Pascal"

    def draw(self, context):
        layout = self.layout
        obj = context.active_object
        if obj is not None and obj.get("pascal_id"):
            box = layout.box()
            box.label(text=f"Type: {obj.get('pascal_type', '?')}")
            box.label(text=f"ID: {obj.get('pascal_id')}")
            params = obj.get("pascal_params")
            if params:
                col = box.column(align=True)
                for key in params.keys():
                    row = col.row()
                    row.label(text=str(key))
                    value = params[key]
                    row.label(text=", ".join(f"{v:.3g}" for v in value) if hasattr(value, "__len__") and not isinstance(value, str) else f"{value}")
            if obj.get("pascal_migrated"):
                box.label(text="Migrated from legacy format", icon="INFO")
        else:
            layout.label(text="No Pascal node selected")

        report = context.scene.get("pascal_import_report")
        if report:
            box = layout.box()
            box.label(text="Import report", icon="TEXT")
            for line in str(report).split("\n")[:15]:
                box.label(text=line[:60])


def register():
    bpy.utils.register_class(PASCAL_PT_node)


def unregister():
    bpy.utils.unregister_class(PASCAL_PT_node)
