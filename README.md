Objective: Develop a Blender 4.0+ addon that allows users to deform (bend) a mesh object along a specified curve object using real-time BMesh manipulation. The addon should provide intuitive controls for animation, deformation axis selection (including negative axes), and aim for high-quality, smooth, and natural-looking deformations with minimal twisting artifacts.
Core Functionality: BMesh Deformation Engine
Real-time Mesh Manipulation:
Utilize Blender's bmesh module for all mesh vertex transformations.
The deformation must be applied non-destructively to the original mesh data by caching original vertex coordinates (in a relevant local space) and applying transformations based on user-defined parameters.
Implement a mechanism to restore the object to its original, undeformed state.
Curve Sampling and Frame Generation:
High-Resolution Sampling: Accurately sample points and tangents from the target curve (Bezier, NURBS, Poly). The number of samples should be adaptive or user-configurable for a balance between accuracy and performance.
Robust Orientation Frames (Twist Control):
For each sampled point on the curve, generate a stable 3D orientation frame (a 3x3 rotation matrix or equivalent set of orthonormal basis vectors: tangent, normal, binormal).
The tangent vector is derived directly from the curve.
The normal and binormal vectors must be calculated to minimize undesirable twisting of the deformed object as it follows the curve. Implement an advanced technique such as:
Rotation Minimizing Frames (RMF): Preferable for highest quality. Research and implement a suitable RMF algorithm (e.g., Double Reflection or a robust projection-based method) if feasible within Python performance constraints.
Advanced Parallel Transport: If full RMF is too complex/slow, implement a sophisticated parallel transport mechanism for the "up" vector (normal) that is more robust than simple projection or direct use of curve tilt. This should consider the previous frame's orientation to propagate the normal smoothly.
The curve's user-adjustable "Tilt" property should be respected as an initial roll or influence on the normal calculation where appropriate.
Vertex Mapping and Transformation:
Deformation Axis: Allow the user to select the object's local deformation axis (X, Y, Z) and its direction (+/-). This axis of the object will be conceptually "laid out" along the curve.
Normalized Mapping: For each vertex of the object:
Calculate its normalized parametric coordinate (u_obj, from 0 to 1) along the selected deformation axis relative to the object's bounding box on that axis. The sign of the chosen axis determines whether min or max extent maps to u_obj = 0.
Animation Along Curve:
An "Animation Factor" (float, keyframable, typically 0-1 range but allowing extrapolation) will control the object's position along the curve.
The curve_sample_u = u_obj - animation_factor will determine the parametric position on the curve to which the object vertex maps.
Transformation:
For each object vertex, calculate its local offset vector perpendicular to its chosen deformation axis.
Using the interpolated orientation frame from the curve at curve_sample_u, transform this local offset vector.
The final world position of the vertex will be the sampled curve point plus this transformed, oriented offset.
Convert this final world position back to the object's local space for application to BMVert.co.
Initial Object Placement and Origin Handling:
Automatic Snapping (Setup Phase): When the bend effect is first applied or the deform axis/curve changes significantly:
Apply the object's existing rotation and scale transforms to its mesh data.
Calculate the object's bounding box based on its (now transformed) local mesh data along the chosen deformation axis.
Shift the object's mesh data (and compensate its matrix_world) so that its origin effectively lies at the "leading edge" corresponding to the chosen deformation axis (e.g., min-X extent if "X" axis is chosen, max-X extent if "-X" is chosen).
Position the object (its new origin) at the start of the target curve.
Orient the object so its chosen (signed) local deformation axis aligns with the curve's initial tangent, and its "up" orientation is reasonably aligned using the curve's initial frame.
The verts_local_at_setup should be cached in this post-setup local space, and the setup_matrix_world (the object's matrix_world after this placement) should also be cached.
The real-time deformation then operates on these verts_local_at_setup relative to the setup_matrix_world and the dynamic curve frames.
Performance Optimization:
Smart Caching:
Cache sampled curve data (points, tangents, orientation frames) and only recompute if the curve object itself or its matrix_world or data.version changes.
Cache the object's initial setup data (verts_local_at_setup, setup_matrix_world, bbox_info) and only recompute if the object's mesh, deform axis, or the target curve (requiring re-placement) changes.
NumPy Integration (Optional but Preferred):
Where feasible, convert vertex coordinate lists and transformation matrices to NumPy arrays to leverage its vectorized operations for batch processing of vertex transformations. This can provide a significant speed-up over per-vertex Python loops for the core math. Be mindful of the overhead of converting data to/from NumPy arrays and BMesh.
Algorithm Efficiency: Profile and optimize critical sections of the Python code, especially loops involved in vertex mapping and transformation. Simplify mathematical calculations where possible without sacrificing quality.
Interactivity and Updates:
Handler Management:
Implement a depsgraph_update_post handler to trigger re-calculation of the deformation when the bent object, its mesh data, its custom properties, or the target curve (object or data) are modified.
The handler should be managed carefully: added only when at least one object has the bend effect active, and removed when no objects are using it, to minimize performance impact on the scene.
The handler should be efficient and avoid redundant updates by checking if relevant parameters have actually changed since the last update.
Custom Properties:
bmesh_bend_active (Bool): Toggles the entire effect on/off for the object.
bmesh_bend_curve_target (PointerProperty): To select the curve object.
bmesh_bend_deform_axis (Enum): (X, -X, Y, -Y, Z, -Z).
bmesh_bend_animation_factor (Float): Keyframable, drives animation along the curve.
bmesh_bend_strength (Float, 0-1): Overall strength/influence of the deformation.
Update callbacks for these properties should trigger the main deformation logic.
User Interface:
Standard N-Panel:
A panel in the 3D View's Sidebar (N-key) for accessing all settings: active toggle, curve picker, deform axis selector, animation factor slider, strength slider.
Include buttons for "Force Full Re-Setup & Update" and "Clear All Cached Data" for debugging and control.
Interactive Hovering Panel (Advanced Stretch Goal):
If time and complexity allow, implement a small, floating UI panel that appears near the selected, actively bent object.
This panel would be driven by a modal operator.
It should provide quick access to the bmesh_bend_animation_factor slider and a button to directly keyframe this property.
Drawing would utilize the gpu module (preferred over bgl).
This panel should intelligently position itself to avoid obscuring the object and handle mouse events for interaction.
Error Handling and Edge Cases:
Implement robust checks for invalid inputs (e.g., no curve selected, curve with no points, object with no vertices, zero-length deform axis).
Provide clear error messages or warnings to the user in the UI or console.
Ensure graceful failure or deactivation of the effect if critical errors occur.
Code Quality and Structure:
Well-commented, modular code.
Clear separation of concerns (UI, core logic, caching, handlers).
Adherence to Python best practices and Blender addon development guidelines.

## Usage

1. Copy the `bmesh_bend` folder to your Blender addons directory or install it as a zip.
2. Enable "BMesh Bend" in the Add-ons preferences.
3. Select a mesh object, enable *Active* in the panel, and pick a target curve.
4. Adjust the deformation axis, animation factor, and strength to bend the mesh along the curve.

## Features

- Real-time bending of mesh objects along curves using BMesh operations
- Orientation frames computed along the curve to reduce twisting
- Supports positive and negative deformation axes
- Simple caching system with buttons to force re-setup and clear cached data

