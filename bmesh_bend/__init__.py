"""BMesh Bend Addon"""
import bpy
import bmesh
from mathutils import Vector, Matrix
from bpy.props import (
    BoolProperty,
    FloatProperty,
    EnumProperty,
    IntProperty,
    PointerProperty,
)

bl_info = {
    "name": "BMesh Bend",
    "author": "Codex",
    "version": (0, 3, 0),
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

# Blender objects do not support weak references, so we store caches in a
# regular dictionary keyed by the object's memory pointer. Entries are removed
# via operators when a reset is requested.
_RUNTIME_CACHE = {}


def _cache_key(obj):
    """Return a dictionary key for *obj* that remains stable for its lifetime."""
    return obj.as_pointer()


def ensure_cache(obj):
    """Return a runtime cache dictionary associated with *obj*."""
    key = _cache_key(obj)
    if key not in _RUNTIME_CACHE:
        _RUNTIME_CACHE[key] = {}
    return _RUNTIME_CACHE[key]


def cache_original_coords(obj):
    cache = ensure_cache(obj)
    if 'orig_coords' not in cache:
        cache['orig_coords'] = [v.co.copy() for v in obj.data.vertices]
        cache['cached_matrix'] = obj.matrix_world.copy()


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

def sample_curve(curve_obj, depsgraph, resolution=64):
    """Sample points, tangents and orientation frames from the curve."""
    eval_obj = curve_obj.evaluated_get(depsgraph)
    curve = eval_obj.to_curve(depsgraph)

    points = []
    tangents = []
    for spline in curve.splines:
        if len(spline.bezier_points) + len(spline.points) < 2:
            continue
        use_eval = hasattr(spline, "evaluate") and hasattr(spline, "evaluate_derivative")
        # Fallback coordinates for simple interpolation when evaluate API is missing
        coords = None
        if not use_eval:
            if len(spline.bezier_points):
                coords = [bp.co.to_3d() for bp in spline.bezier_points]
            else:
                coords = [Vector((p.co.x, p.co.y, p.co.z)) / (p.co.w if p.co.w else 1.0) for p in spline.points]
        for i in range(resolution + 1):
            u = i / resolution
            if use_eval:
                co = eval_obj.matrix_world @ spline.evaluate(u)
                tan = eval_obj.matrix_world.to_3x3() @ spline.evaluate_derivative(u)
            else:
                seg = u * (len(coords) - 1)
                idx = int(seg)
                frac = seg - idx
                if idx >= len(coords) - 1:
                    idx = len(coords) - 2
                    frac = 1.0
                p0 = coords[idx]
                p1 = coords[idx + 1]
                co = eval_obj.matrix_world @ p0.lerp(p1, frac)
                tan = eval_obj.matrix_world.to_3x3() @ (p1 - p0)
            points.append(co)
            tangents.append(tan.normalized())

    frames = []
    lengths = []
    if not points:
        return [], [], []
    up = Vector((0, 0, 1))
    normal = (up - up.dot(tangents[0]) * tangents[0]).normalized()
    prev = tangents[0]
    total_len = 0.0
    lengths.append(total_len)
    for i, t in enumerate(tangents):
        binormal = t.cross(normal).normalized()
        frames.append((t.normalized(), normal.normalized(), binormal))
        axis = prev.cross(t)
        if axis.length > 1e-6:
            angle = prev.angle(t)
            rot = Matrix.Rotation(angle, 3, axis.normalized())
            normal = (rot @ normal).normalized()
        prev = t
        if i > 0:
            total_len += (points[i] - points[i-1]).length
            lengths.append(total_len)
    return points, frames, lengths

# -----------------------------------------------------------------------------
# Deformation
# -----------------------------------------------------------------------------

def deform_object(obj, curve_obj, deform_axis='X', anim_factor=0.0, strength=1.0, depsgraph=None):
    """Deform *obj* along *curve_obj* using cached coordinates."""
    cache_original_coords(obj)
    cache = ensure_cache(obj)
    orig_coords = cache['orig_coords']
    cached_matrix = cache.get('cached_matrix', obj.matrix_world)

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    axis_idx, axis_sign = AXIS_MAP[deform_axis]
    bbox_min = min(co[axis_idx] for co in orig_coords)
    bbox_max = max(co[axis_idx] for co in orig_coords)
    bbox_len = max(bbox_max - bbox_min, 1e-6)

    if depsgraph is None:
        depsgraph = bpy.context.evaluated_depsgraph_get()
    points, frames, lengths = sample_curve(curve_obj, depsgraph, resolution=obj.bmesh_bend_resolution)
    if not points:
        bm.free()
        return

    def frame_to_quat(frame):
        mat = Matrix((frame[0], frame[1], frame[2])).transposed()
        return mat.to_quaternion()

    quats = [frame_to_quat(f) for f in frames]
    curve_len = lengths[-1] if lengths else 0.0
    start_pos = anim_factor * max(curve_len - bbox_len, 0.0)

    for v, orig in zip(bm.verts, orig_coords):
        s = start_pos + (orig[axis_idx] - bbox_min)
        s = max(0.0, min(curve_len, s))
        i1 = 0
        while i1 < len(lengths) and lengths[i1] < s:
            i1 += 1
        if i1 >= len(lengths):
            i1 = len(lengths) - 1
        i0 = max(i1 - 1, 0)
        seg_len = lengths[i1] - lengths[i0]
        frac = 0.0 if seg_len == 0.0 else (s - lengths[i0]) / seg_len
        point = points[i0].lerp(points[i1], frac)
        quat = quats[i0].slerp(quats[i1], frac)
        mat = quat.to_matrix()
        if axis_sign == -1:
            mat[0] *= -1
        offset = orig.copy()
        offset[axis_idx] = 0.0
        world_offset = cached_matrix.to_3x3() @ offset
        new_world = point + mat @ world_offset * strength
        v.co = obj.matrix_world.inverted() @ new_world

    bm.to_mesh(obj.data)
    bm.free()

# -----------------------------------------------------------------------------
# Update logic
# -----------------------------------------------------------------------------

def update_bend(obj, context=None, depsgraph=None):
    if not obj.bmesh_bend_active or not obj.bmesh_bend_curve_target:
        restore_original_coords(obj)
        return
    if depsgraph is None:
        if context is not None:
            depsgraph = context.evaluated_depsgraph_get()
        else:
            depsgraph = bpy.context.evaluated_depsgraph_get()
    deform_object(
        obj,
        obj.bmesh_bend_curve_target,
        obj.bmesh_bend_deform_axis,
        obj.bmesh_bend_animation_factor,
        obj.bmesh_bend_strength,
        depsgraph=depsgraph,
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
        key = _cache_key(obj)
        if key in _RUNTIME_CACHE:
            del _RUNTIME_CACHE[key]
        update_bend(obj, context)
        return {'FINISHED'}


class BMBEND_OT_clear_cache(bpy.types.Operator):
    bl_idname = "object.bmbend_clear_cache"
    bl_label = "Clear Cached Data"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        key = _cache_key(obj)
        if key in _RUNTIME_CACHE:
            del _RUNTIME_CACHE[key]
        return {'FINISHED'}


class BMBEND_OT_insert_keyframe(bpy.types.Operator):
    """Insert a keyframe for the Animation Factor"""
    bl_idname = "object.bmbend_insert_keyframe"
    bl_label = "Keyframe Animation Factor"

    @classmethod
    def poll(cls, context):
        return context.object and context.object.type == 'MESH'

    def execute(self, context):
        obj = context.object
        obj.keyframe_insert(data_path="bmesh_bend_animation_factor")
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
        row = layout.row(align=True)
        row.prop(obj, 'bmesh_bend_animation_factor', slider=True)
        row.operator('object.bmbend_insert_keyframe', text='', icon='KEY_HLT')
        layout.prop(obj, 'bmesh_bend_resolution')
        layout.prop(obj, 'bmesh_bend_strength')
        layout.operator('object.bmbend_setup')
        layout.operator('object.bmbend_clear_cache')

classes = (
    BMBEND_OT_setup,
    BMBEND_OT_clear_cache,
    BMBEND_OT_insert_keyframe,
    BMBEND_PT_panel,
)

# -----------------------------------------------------------------------------
# Handlers and registration
# -----------------------------------------------------------------------------

def depsgraph_update(scene, depsgraph):
    processed = set()
    for update in depsgraph.updates:
        if isinstance(update.id, bpy.types.Object):
            obj = update.id
            if getattr(obj, 'bmesh_bend_active', False):
                key = _cache_key(obj)
                if key not in processed:
                    update_bend(obj, depsgraph)
                    processed.add(key)

def frame_change(_scene):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    for obj in bpy.data.objects:
        if getattr(obj, 'bmesh_bend_active', False):
            update_bend(obj, depsgraph=depsgraph)

def register_props():
    """Register custom properties on ``bpy.types.Object``.

    Blender may keep property definitions from a previously loaded version of
    the addon.  To avoid "registration error: property ... already registered"
    messages, remove any existing definitions before creating the new ones.
    """

    # Always remove any previous definitions first to ensure a clean state.
    unregister_props()

    # Remove leftover ID properties that could prevent registration.  When
    # the addon is enabled, ``bpy.data`` may be a restricted proxy that does
    # not expose ``objects``.  If so, defer the cleanup using a timer so it
    # runs once registration is complete and unrestricted access is restored.
    props = [
        "bmesh_bend_active",
        "bmesh_bend_curve_target",
        "bmesh_bend_deform_axis",
        "bmesh_bend_animation_factor",
        "bmesh_bend_resolution",
        "bmesh_bend_strength",
    ]

    def _cleanup_idprops():
        if hasattr(bpy.data, "objects"):
            for obj in bpy.data.objects:
                for attr in props:
                    if attr in obj.keys():
                        try:
                            del obj[attr]
                        except Exception:
                            pass
        return None

    if hasattr(bpy.data, "objects"):
        _cleanup_idprops()
    else:
        bpy.app.timers.register(_cleanup_idprops, first_interval=0.1)

    for attr in props:
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)

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
        min=0.0,
        max=1.0,
        subtype='FACTOR',
        update=update_bend,
    )
    bpy.types.Object.bmesh_bend_resolution = IntProperty(
        name="Resolution",
        default=128,
        min=8,
        max=512,
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
    props = [
        "bmesh_bend_active",
        "bmesh_bend_curve_target",
        "bmesh_bend_deform_axis",
        "bmesh_bend_animation_factor",
        "bmesh_bend_resolution",
        "bmesh_bend_strength",
    ]
    for attr in props:
        if hasattr(bpy.types.Object, attr):
            delattr(bpy.types.Object, attr)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    register_props()
    bpy.app.handlers.depsgraph_update_post.append(depsgraph_update)
    bpy.app.handlers.frame_change_post.append(frame_change)

def unregister():
    unregister_props()
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
    if depsgraph_update in bpy.app.handlers.depsgraph_update_post:
        bpy.app.handlers.depsgraph_update_post.remove(depsgraph_update)
    if frame_change in bpy.app.handlers.frame_change_post:
        bpy.app.handlers.frame_change_post.remove(frame_change)

if __name__ == "__main__":
    register()
