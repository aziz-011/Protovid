"""Create examples/models/dosing_pump_demo.glb - a simple stand-in 3D assembly.

It only exists so you can try ``--model`` without your own CAD file. Export
your real assembly from your CAD software as STEP (or GLB/OBJ/STL) instead.
Part names are what Claude sees, so keep meaningful names in the CAD tree.
"""

from pathlib import Path

import numpy as np
import trimesh
from trimesh.creation import box, cylinder, annulus

out_dir = Path(__file__).with_name("models")
out_dir.mkdir(exist_ok=True)

scene = trimesh.Scene()


def add(name, mesh, translate=(0, 0, 0), rotate=None):
    if rotate is not None:
        axis, angle = rotate
        mesh.apply_transform(trimesh.transformations.rotation_matrix(np.radians(angle), axis))
    mesh.apply_translation(translate)
    scene.add_geometry(mesh, node_name=name, geom_name=name)


# Z is up in this demo; exported as glTF (Y-up), see the y/z rotation below.
add("support_plate", box(extents=(160, 10, 240)), (0, -5, 0))
add("pump_body", cylinder(radius=45, height=40, sections=64), (0, 25, 30), rotate=([1, 0, 0], 90))
add("rotor", annulus(r_min=8, r_max=32, height=12, sections=64), (0, 50, 30), rotate=([1, 0, 0], 90))
for k, ang in enumerate((0, 120, 240)):
    x, z = 22 * np.cos(np.radians(ang)), 22 * np.sin(np.radians(ang))
    add(f"roller:{k + 1}", cylinder(radius=6, height=14, sections=32), (x, 52, 30 + z), rotate=([1, 0, 0], 90))
add("lower_lid", box(extents=(110, 8, 60)), (0, 50, -40))
add("stepper_motor", cylinder(radius=28, height=50, sections=48), (0, -35, 30), rotate=([1, 0, 0], 90))
add("inlet_tube", cylinder(radius=4, height=120, sections=24), (-55, 40, 95))
add("outlet_tube", cylinder(radius=4, height=120, sections=24), (55, 40, 95))
add("bag_connector", cylinder(radius=7, height=18, sections=32), (-55, 40, 160))
for k, (x, z) in enumerate(((-65, 100), (65, 100), (-65, -100), (65, -100))):
    add(f"screw_m4:{k + 1}", cylinder(radius=4, height=14, sections=24), (x, 5, z), rotate=([1, 0, 0], 90))

# glTF is Y-up: rotate Z-up geometry so it stands upright in any viewer
scene.apply_transform(trimesh.transformations.rotation_matrix(np.radians(-90), [1, 0, 0]))
path = out_dir / "dosing_pump_demo.glb"
scene.export(path)
print(path)
