"""BMesh Bend Addon"""
import bpy
import bmesh
from mathutils import Vector, Matrix
from bpy.props import BoolProperty, FloatProperty, EnumProperty, PointerProperty

bl_info = {
    "name": "BMesh Bend",
    "author": "Codex",
    "version": (0, 2, 0),
    "blender": (4, 0, 0),
    "description": "Deform mesh along curve using BMesh",
    "category": "Object",
}

AXIS_MAP = {
    'X': (0, 1),
    '-X': (0, -1),
    'Y': (1, 1),
    '-Y': (1, -1),
    'Z': (2, 1),
    '-Z': (2, -1),
}

# -----------------------------------------------------------------------------
# Cache helpers
# -----------------------------------------------------------------------------

def ensure_cache(obj):
    if 'bmesh_bend_cache' not in obj:
        obj['bmesh_bend_cache'] = {}
    return obj['bmesh_bend_cache']


def cache_original_coords(obj):
    cache = ensure_cache(obj)
    if 'orig_coords' not in cache:
        cache['orig_coords'] = [v.co.copy() for v in obj.data.vertices]


def restore_original_coords(obj):
    cache = ensure_cache(obj)
    if 'orig_coords' in cache:
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        for v, co in zip(bm.verts, cache['orig_coords']):
            v.co = co.copy()
        bm.to_mesh(obj.data)
        bm.free()

# -----------------------------------------------------------------------------
# Curve sampling and frames
# -----------------------------------------------------------------------------

def sample_curve(curve_obj, resolution=64):
    """Sample points, tangents and orientation frames from the curve."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    eval_obj = curve_obj.evaluated_get(depsgraph)
    curve = eval_obj.to_curve(depsgraph)

    points = []
    tangents = []
    for spline in curve.splines:
        if len(spline.bezier_points) + len(spline.points) < 2:
            continue
        for i in range(resolution + 1):
            u = i / resolution
            co = eval_obj.matrix_world @ spline.evaluate(u)
            tan = eval_obj.matrix_world.to_3x3() @ spline.evaluate_derivative(u)
            points.append(co)
            tangents.append(tan.normalized())

    frames = []
    if not points:
        return [], []
    up = Vector((0, 0, 1))
    normal = (up - up.dot(tangents[0]) * tangents[0]).normalized()
    prev = tangents[0]
    for t in tangents:
        binormal = t.cross(normal).normalized()
        frames.append((t.normalized(), normal.normalized(), binormal))
        axis = prev.cross(t)
        if axis.length > 1e-6:
            angle = prev.angle(t)
            rot = Matrix.Rotation(angle, 3, axis.normalized())
            normal = (rot @ normal).normalized()
        prev = t
    return points, frames

# -----------------------------------------------------------------------------
# Deformation
# -----------------------------------------------------------------------------

def deform_object(obj, curve_obj, deform_axis='X', anim_factor=0.0, strength=1.0):
    cache_original_coords(obj)
    bm = bmesh.new()
    bm.from_mesh(obj.data)

    axis_idx, axis_sign = AXIS_MAP[deform_axis]
    verts = [v.co.copy() for v in bm.verts]
    bbox_min = min(v[axis_idx] for v in verts)
    bbox_max = max(v[axis_idx] for v in verts)

    points, frames = sample_curve(curve_obj, resolution=128)
    if not points:
        bm.free()
        return

    for v, orig in zip(bm.verts, verts):
        u = (orig[axis_idx] - bbox_min) / max(bbox_max - bbox_min, 1e-6)
        u = u - anim_factor
        idx = int(max(0, min(len(points) - 1, u * (len(points) - 1))))
        point = points[idx]
        tan, nor, binor = frames[idx]
        offset = orig.copy()
        offset[axis_idx] = 0
        mat = Matrix((tan, nor, binor)).transposed()
        if axis_sign == -1:
            mat[0] *= -1
        new_co = point + mat @ offset * strength
        v.co = obj.matrix_world.inverted() @ new_co

    bm.to_mesh(obj.data)
    bm.free()

# -----------------------------------------------------------------------------
# Update logic
# -----------------------------------------------------------------------------

def update_bend(obj, _context=None):
    if not obj.bmesh_bend_active or not obj.bmesh_bend_curve_target:
        restore_original_coords(obj)
        return
    deform_object(
        obj,
        obj.bmesh_bend_curve_target,
        obj.bmesh_bend_deform_axis,
        obj.bmesh_bend_animation_factor,
        obj.bmesh_bend_strength,
    )

# -----------------------------------------------------------------------------
# Operators and UI
# -----------------------------------------------------------------------------

class BMBEND_OT_setup(bpy.types.Operator):
    bl_idname = "object.bmbend_setup"
    bl_label = "Force Re-Setup"
    bl_description = "Clear cache and recompute initial data"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        if 'bmesh_bend_cache' in obj:
            del obj['bmesh_bend_cache']
        update_bend(obj)
        return {'FINISHED'}


class BMBEND_OT_clear_cache(bpy.types.Operator):
    bl_idname = "object.bmbend_clear_cache"
    bl_label = "Clear Cached Data"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        if 'bmesh_bend_cache' in obj:
            del obj['bmesh_bend_cache']
        return {'FINISHED'}


class BMBEND_PT_panel(bpy.types.Panel):
    bl_label = "BMesh Bend"
    bl_idname = "OBJECT_PT_bmesh_bend"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'BMesh Bend'

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def draw(self, context):
        obj = context.object
        layout = self.layout
        layout.prop(obj, 'bmesh_bend_active')
        layout.prop(obj, 'bmesh_bend_curve_target')
        layout.prop(obj, 'bmesh_bend_deform_axis')
        layout.prop(obj, 'bmesh_bend_animation_factor')
        layout.prop(obj, 'bmesh_bend_strength')
        layout.operator('object.bmbend_setup')
        layout.operator('object.bmbend_clear_cache')

classes = (
    BMBEND_OT_setup,
    BMBEND_OT_clear_cache,
    BMBEND_PT_panel,
)

# -----------------------------------------------------------------------------
# Handlers and registration
# -----------------------------------------------------------------------------

def depsgraph_update(scene, depsgraph):
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Object):
            obj = update.id
            if getattr(obj, 'bmesh_bend_active', False):
                update_bend(obj)

def register_props():
    bpy.types.Object.bmesh_bend_active = BoolProperty(
        name="Active",
        default=False,
        update=update_bend,
    )
    bpy.types.Object.bmesh_bend_curve_target = PointerProperty(
        name="Curve",
        type=bpy.types.Object,
        update=update_bend,
    )
    bpy.types.Object.bmesh_bend_deform_axis = EnumProperty(
        name="Axis",
        items=[('X', 'X', ''), ('-X', '-X', ''),
               ('Y', 'Y', ''), ('-Y', '-Y', ''),
               ('Z', 'Z', ''), ('-Z', '-Z', '')],
        default='X',
        update=update_bend,
    )
    bpy.types.Object.bmesh_bend_animation_factor = FloatProperty(
        name="Animation Factor",
        default=0.0,
        update=update_bend,
    )
    bpy.types.Object.bmesh_bend_strength = FloatProperty(
        name="Strength",
        default=1.0,
        min=0.0,
        max=1.0,
        update=update_bend,
    )

def unregister_props():
    del bpy.types.Object.bmesh_bend_active
    del bpy.types.Object.bmesh_bend_curve_target
    del bpy.types.Object.bmesh_bend_deform_axis
    del bpy.types.Object.bmesh_bend_animation_factor
    del bpy.types.Object.bmesh_bend_strength

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()
    bpy.app.handlers.depsgraph_update_post.append(depsgraph_update)

def unregister():
    unregister_props()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update)

if __name__ == "__main__":
    register()
