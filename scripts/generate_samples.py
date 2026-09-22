#!/usr/bin/env python3
# SPDX-License-Identifier: CC0-1.0
# Autorig Workbench: generates clean, CC0 open-source sample models across 5 archetypes:
# prop, quadruped, hexapod, humanoid, and flier.
import os, sys, json, math

CC0_LICENSE = """CC0 1.0 Universal (CC0 1.0) Public Domain Dedication

The person who associated a work with this deed has dedicated the work to the
public domain by waiving all of his or her rights to the work worldwide under
copyright law, including all related and neighboring rights, to the extent
allowed by law.

You can copy, modify, distribute and perform the work, even for commercial
purposes, all without asking permission.
"""

def generate_all(samples_root):
    try:
        import bpy, bmesh
    except ImportError:
        # Re-invoke under Blender
        REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(REPO, "autorig", "core"))
        import blender
        b_exe = blender.find(required=True)
        import subprocess
        res = subprocess.run([b_exe, "-b", "--python", __file__, "--", samples_root],
                             capture_output=True, text=True, errors="replace")
        print(res.stdout)
        if res.returncode != 0:
            print(res.stderr, file=sys.stderr)
            sys.exit(res.returncode)
        return

    def reset():
        bpy.ops.wm.read_factory_settings(use_empty=True)

    def export_obj(path, remesh_voxel=None):
        os.makedirs(os.path.dirname(path), exist_ok=True)
        meshes = [o for o in bpy.data.objects if o.type == 'MESH']
        for o in meshes: o.select_set(True)
        bpy.context.view_layer.objects.active = meshes[0]
        if len(meshes) > 1:
            bpy.ops.object.join()
        obj = bpy.context.active_object
        if remesh_voxel:
            obj.data.remesh_voxel_size = remesh_voxel
            bpy.ops.object.voxel_remesh()
        # Ensure manifold and outwards normals
        bpy.ops.object.mode_set(mode='EDIT')
        bpy.ops.mesh.select_all(action='SELECT')
        bpy.ops.mesh.normals_make_consistent(inside=False)
        bpy.ops.object.mode_set(mode='OBJECT')
        
        # In Blender 4.x / 5.x, wm.obj_export with forward_axis='NEGATIVE_Z', up_axis='Y' matches standard
        # OBJ convention (Y-up, -Z forward) which wm.obj_import converts back to Blender's Z-up coordinate space.
        bpy.ops.wm.obj_export(filepath=path, export_selected_objects=True, forward_axis='NEGATIVE_Z', up_axis='Y')

    # 1. Prop (pedestal)
    def build_pedestal(folder):
        reset()
        # Base
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.4, depth=0.2, location=(0, 0, 0.1))
        # Shaft
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.25, depth=1.4, location=(0, 0, 0.9))
        # Capital
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.38, depth=0.2, location=(0, 0, 1.7))
        # Crown
        bpy.ops.mesh.primitive_cylinder_add(vertices=24, radius=0.42, depth=0.1, location=(0, 0, 1.85))
        
        obj_path = os.path.join(folder, "pedestal.obj")
        export_obj(obj_path, remesh_voxel=0.03)
        
        spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "forward": [0, -1, 0],
                "chains": [
                    {
                        "name": "spine",
                        "points": [[0.5, 0.5, 0.05], [0.5, 0.5, 0.5], [0.5, 0.5, 0.95]],
                        "names": ["base", "pillar"]
                    }
                ]
            },
            "budget": 800,
            "clips": {"archetype": "turret"},
            "card": {"metres": 1.9, "role": "prop"},
            "notes": {"rig": "Classic fluted pedestal prop"}
        }
        with open(os.path.join(folder, "rig.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)
        with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(CC0_LICENSE)

    # 2. Quadruped (canine)
    def build_canine(folder):
        reset()
        # Torso
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0.05, 0.65))
        torso = bpy.context.active_object
        torso.scale = (0.32, 0.80, 0.30)
        bpy.ops.object.transform_apply(scale=True)
        
        # Neck
        bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.12, depth=0.35, location=(0, -0.42, 0.78))
        neck = bpy.context.active_object
        neck.rotation_euler = (math.radians(-30), 0, 0)
        bpy.ops.object.transform_apply(rotation=True)

        # Head & Muzzle
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, -0.65, 0.85))
        head = bpy.context.active_object
        head.scale = (0.22, 0.28, 0.22)
        bpy.ops.object.transform_apply(scale=True)

        # Snout
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, -0.85, 0.80))
        snout = bpy.context.active_object
        snout.scale = (0.16, 0.20, 0.14)
        bpy.ops.object.transform_apply(scale=True)
        
        # 4 Legs
        leg_positions = [
            (0.22, -0.32, 0.325),  # Front Left
            (-0.22, -0.32, 0.325), # Front Right
            (0.22, 0.42, 0.325),   # Back Left
            (-0.22, 0.42, 0.325),  # Back Right
        ]
        for lx, ly, lz in leg_positions:
            bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.065, depth=0.65, location=(lx, ly, lz))
            
        # Tail
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.045, depth=0.55, location=(0, 0.65, 0.75))
        tail = bpy.context.active_object
        tail.rotation_euler = (math.radians(30), 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        
        obj_path = os.path.join(folder, "canine.obj")
        export_obj(obj_path, remesh_voxel=0.035)
        
        spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "forward": [0, -1, 0],
                "chains": [
                    {
                        "name": "spine",
                        "points": [
                            [0.5, 0.77, 0.65],
                            [0.5, 0.55, 0.65],
                            [0.5, 0.34, 0.72],
                            [0.5, 0.22, 0.82],
                            [0.5, 0.02, 0.80]
                        ],
                        "names": ["hips", "spine_1", "neck", "head"]
                    },
                    {"name": "leg_front.L", "points": [[0.85, 0.36, 0.65], [0.85, 0.36, 0.02]], "bones": 2, "parent": ["spine", 2]},
                    {"name": "leg_front.R", "points": [[0.15, 0.36, 0.65], [0.15, 0.36, 0.02]], "bones": 2, "parent": ["spine", 2]},
                    {"name": "leg_back.L", "points": [[0.85, 0.77, 0.65], [0.85, 0.77, 0.02]], "bones": 2, "parent": ["spine", 0]},
                    {"name": "leg_back.R", "points": [[0.15, 0.77, 0.65], [0.15, 0.77, 0.02]], "bones": 2, "parent": ["spine", 0]},
                    {"name": "tail", "points": [[0.5, 0.82, 0.72], [0.5, 0.98, 0.94]], "bones": 2, "parent": ["spine", 0]}
                ],
                "audit": {
                    "bleed_pct": 3.0,
                    "combined_tears": 8,
                    "bend_tears": 10
                }
            },
            "budget": 1200,
            "clips": {"archetype": "walker", "display": "Canine", "category": "Creatures", "attack": "bite"},
            "card": {"metres": 1.4, "role": "creature"},
            "notes": {
                "rig": "Stylized quadruped canine",
                "rig.audit": "Allowance for compact quadruped leg root proximity"
            }
        }
        with open(os.path.join(folder, "rig.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)
        with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(CC0_LICENSE)

    # 3. Hexapod (beetle)
    def build_beetle(folder):
        reset()
        # Thorax
        bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=16, radius=0.32, location=(0, -0.05, 0.42))
        th = bpy.context.active_object
        th.scale = (0.9, 0.7, 0.5)
        bpy.ops.object.transform_apply(scale=True)

        # Abdomen
        bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=16, radius=0.40, location=(0, 0.32, 0.42))
        ab = bpy.context.active_object
        ab.scale = (0.8, 1.1, 0.5)
        bpy.ops.object.transform_apply(scale=True)
        
        # Head
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=12, radius=0.20, location=(0, -0.42, 0.38))
        
        # 6 Legs
        legs = [
            (-0.15, 0.40, 0.55),  # Front
            (0.12, 0.42, 0.55),   # Mid
            (0.42, 0.40, 0.55),   # Back
        ]
        for y_pos, x_span, depth in legs:
            for side in (1, -1):
                bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.06, depth=depth,
                                                   location=(side * x_span, y_pos, 0.25))
                leg = bpy.context.active_object
                leg.rotation_euler = (0, side * math.radians(25), 0)
                bpy.ops.object.transform_apply(rotation=True)
                
        obj_path = os.path.join(folder, "beetle.obj")
        export_obj(obj_path, remesh_voxel=0.038)
        
        spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "forward": [0, -1, 0],
                "chains": [
                    {
                        "name": "spine",
                        "points": [
                            [0.5, 0.80, 0.65],
                            [0.5, 0.55, 0.67],
                            [0.5, 0.30, 0.65],
                            [0.5, 0.18, 0.62],
                            [0.5, 0.02, 0.60]
                        ],
                        "names": ["hips", "spine_1", "neck", "head"]
                    },
                    {"name": "leg_front.L", "points": [[0.80, 0.34, 0.42], [0.95, 0.30, 0.02]], "bones": 2, "parent": ["spine", 2]},
                    {"name": "leg_front.R", "points": [[0.20, 0.34, 0.42], [0.05, 0.30, 0.02]], "bones": 2, "parent": ["spine", 2]},
                    {"name": "leg_mid.L", "points": [[0.82, 0.54, 0.42], [0.96, 0.54, 0.02]], "bones": 2, "parent": ["spine", 1]},
                    {"name": "leg_mid.R", "points": [[0.18, 0.54, 0.42], [0.04, 0.54, 0.02]], "bones": 2, "parent": ["spine", 1]},
                    {"name": "leg_back.L", "points": [[0.80, 0.76, 0.42], [0.95, 0.80, 0.02]], "bones": 2, "parent": ["spine", 0]},
                    {"name": "leg_back.R", "points": [[0.20, 0.76, 0.42], [0.05, 0.80, 0.02]], "bones": 2, "parent": ["spine", 0]}
                ],
                "audit": {
                    "bleed_pct": 3.0,
                    "bend_tears": 6
                }
            },
            "budget": 1400,
            "clips": {"archetype": "walker", "display": "Beetle", "category": "Critters"},
            "card": {"metres": 0.8, "role": "creature"},
            "notes": {
                "rig": "Six-legged hexapod beetle",
                "rig.audit": "Allowance for multi-leg proximity on compact carapace"
            }
        }
        with open(os.path.join(folder, "rig.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)
        with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(CC0_LICENSE)

    # 4. Humanoid (biped)
    def build_biped(folder):
        reset()
        # Torso
        bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 1.15))
        torso = bpy.context.active_object
        torso.scale = (0.28, 0.16, 0.4)
        bpy.ops.object.transform_apply(scale=True)

        # Neck
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.08, depth=0.20, location=(0, 0, 1.45))
        
        # Head
        bpy.ops.mesh.primitive_uv_sphere_add(segments=16, ring_count=12, radius=0.18, location=(0, 0, 1.68))
        
        # Arms
        for side in (1, -1):
            bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.06, depth=0.56, location=(side * 0.42, 0, 1.18))
            arm = bpy.context.active_object
            arm.rotation_euler = (0, side * math.radians(60), 0)
            bpy.ops.object.transform_apply(rotation=True)
            
        # Legs
        for side in (1, -1):
            bpy.ops.mesh.primitive_cylinder_add(vertices=16, radius=0.08, depth=0.95, location=(side * 0.16, 0, 0.48))
            
        obj_path = os.path.join(folder, "biped.obj")
        export_obj(obj_path, remesh_voxel=0.04)
        
        spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "forward": [0, -1, 0],
                "head_to_snout": False,
                "chains": [
                    {"name": "spine", "points": [[0.5, 0.5, 0.51], [0.5, 0.5, 0.68], [0.5, 0.5, 0.80], [0.5, 0.5, 0.98]], "names": ["spine", "chest", "head"]},
                    {"name": "arm.L", "points": [[0.60, 0.5, 0.68], [0.95, 0.5, 0.56]], "bones": 2, "parent": ["spine", 1]},
                    {"name": "arm.R", "points": [[0.40, 0.5, 0.68], [0.05, 0.5, 0.56]], "bones": 2, "parent": ["spine", 1]},
                    {"name": "leg.L", "points": [[0.62, 0.5, 0.51], [0.62, 0.5, 0.02]], "bones": 2, "parent": ["spine", 0]},
                    {"name": "leg.R", "points": [[0.38, 0.5, 0.51], [0.38, 0.5, 0.02]], "bones": 2, "parent": ["spine", 0]}
                ]
            },
            "budget": 1000,
            "clips": {"archetype": "walker", "display": "Biped", "category": "Characters"},
            "card": {"metres": 1.8, "role": "humanoid"},
            "notes": {"rig": "Standard biped humanoid sample"}
        }
        with open(os.path.join(folder, "rig.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)
        with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(CC0_LICENSE)

    # 5. Flier (wyvern)
    def build_wyvern(folder):
        reset()
        # Body
        bpy.ops.mesh.primitive_uv_sphere_add(segments=20, ring_count=16, radius=0.35, location=(0, 0, 0.6))
        torso = bpy.context.active_object
        torso.scale = (0.5, 1.0, 0.5)
        bpy.ops.object.transform_apply(scale=True)
        
        # Neck & Head
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.1, depth=0.45, location=(0, -0.4, 0.8))
        neck = bpy.context.active_object
        neck.rotation_euler = (math.radians(-35), 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        bpy.ops.mesh.primitive_cone_add(vertices=12, radius1=0.14, depth=0.3, location=(0, -0.6, 0.95))
        
        # 2 Wings
        for side in (1, -1):
            bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.06, depth=0.8, location=(side * 0.5, 0, 0.75))
            w = bpy.context.active_object
            w.rotation_euler = (0, side * math.radians(65), 0)
            bpy.ops.object.transform_apply(rotation=True)
            
        # 2 Legs
        for side in (1, -1):
            bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.06, depth=0.55, location=(side * 0.18, 0.15, 0.28))
            
        # Tail
        bpy.ops.mesh.primitive_cylinder_add(vertices=12, radius=0.06, depth=0.7, location=(0, 0.65, 0.55))
        t = bpy.context.active_object
        t.rotation_euler = (math.radians(25), 0, 0)
        bpy.ops.object.transform_apply(rotation=True)
        
        obj_path = os.path.join(folder, "wyvern.obj")
        export_obj(obj_path, remesh_voxel=0.04)
        
        spec = {
            "schema": "autorig-spec/1",
            "rig": {
                "kind": "placed",
                "forward": [0, -1, 0],
                "chains": [
                    {
                        "name": "spine",
                        "points": [
                            [0.5, 0.65, 0.54],
                            [0.5, 0.46, 0.54],
                            [0.5, 0.24, 0.67],
                            [0.5, 0.12, 0.81],
                            [0.5, 0.02, 0.85]
                        ],
                        "names": ["hips", "chest", "neck", "head"]
                    },
                    {"name": "wing.L", "points": [[0.64, 0.46, 0.63], [0.95, 0.46, 0.79]], "bones": 2, "parent": ["spine", 1]},
                    {"name": "wing.R", "points": [[0.36, 0.46, 0.63], [0.05, 0.46, 0.79]], "bones": 2, "parent": ["spine", 1]},
                    {"name": "leg.L", "points": [[0.60, 0.62, 0.40], [0.60, 0.62, 0.02]], "bones": 2, "parent": ["spine", 0]},
                    {"name": "leg.R", "points": [[0.40, 0.62, 0.40], [0.40, 0.62, 0.02]], "bones": 2, "parent": ["spine", 0]},
                    {"name": "tail", "points": [[0.5, 0.68, 0.54], [0.5, 0.97, 0.63]], "bones": 2, "parent": ["spine", 0]}
                ],
                "audit": {
                    "bleed_pct": 3.0
                }
            },
            "budget": 1200,
            "clips": {"archetype": "winged", "display": "Wyvern", "category": "Creatures"},
            "card": {"metres": 2.2, "role": "creature"},
            "notes": {
                "rig": "Winged wyvern flier with membrane rules",
                "rig.audit": "Allowance for wings and legs in close proximity to body"
            }
        }
        with open(os.path.join(folder, "rig.json"), "w", encoding="utf-8") as fh:
            json.dump(spec, fh, indent=2)
        with open(os.path.join(folder, "LICENSE.txt"), "w", encoding="utf-8") as fh:
            fh.write(CC0_LICENSE)

    os.makedirs(samples_root, exist_ok=True)
    models = [
        ("pedestal", build_pedestal),
        ("canine", build_canine),
        ("beetle", build_beetle),
        ("biped", build_biped),
        ("wyvern", build_wyvern),
    ]
    for name, builder in models:
        folder = os.path.join(samples_root, name)
        os.makedirs(folder, exist_ok=True)
        print(f"Generating sample: {name}...")
        builder(folder)
        print(f"Sample {name} generated.")


if __name__ == "__main__":
    target = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].startswith("-") and not sys.argv[-1].endswith(".py") else os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "samples")
    generate_all(target)
