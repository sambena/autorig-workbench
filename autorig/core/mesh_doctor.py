# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: Mesh Doctor & Pre-flight Geometric Healer.
#
# Diagnoses and auto-heals mesh defects that cause Blender skinning and bone heat failures:
#   1. Loose floating vertices and edges (stops heat diffusion).
#   2. Degenerate zero-area faces and collinear edges (causes Laplacian solver singularities).
#   3. Coincident duplicate vertices along unstitched seams.
#   4. Inverted normals and conflicting face winding.
#   5. Non-manifold edge junctions (>2 faces sharing an edge).
#
# Provides both pure-Python geometry inspection (no Blender needed) and Blender BMesh auto-healing.

import math
import os
import sys

try:
    import bpy
    import bmesh
    HAVE_BLENDER = True
except ImportError:
    HAVE_BLENDER = False


# ---------------------------------------------------------------------------------------------------------------
# Pure-Python Mesh Diagnostics (Runs anywhere without Blender)
# ---------------------------------------------------------------------------------------------------------------

def parse_obj_mesh(filepath):
    """Extracts vertices (list of [x,y,z]) and faces (list of list of vertex indices) from an OBJ file."""
    verts = []
    faces = []
    with open(filepath, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            parts = line.split()
            if parts[0] == "v" and len(parts) >= 4:
                verts.append([float(parts[1]), float(parts[2]), float(parts[3])])
            elif parts[0] == "f" and len(parts) >= 4:
                f = []
                for p in parts[1:]:
                    idx = p.split("/")[0]
                    if idx:
                        v_i = int(idx)
                        f.append(v_i - 1 if v_i > 0 else len(verts) + v_i)
                if len(f) >= 3:
                    faces.append(f)
    return verts, faces


def triangle_area(v0, v1, v2):
    """Computes cross-product area of a 3D triangle."""
    ax, ay, az = v1[0] - v0[0], v1[1] - v0[1], v1[2] - v0[2]
    bx, by, bz = v2[0] - v0[0], v2[1] - v0[1], v2[2] - v0[2]
    cx = ay * bz - az * by
    cy = az * bx - ax * bz
    cz = ax * by - ay * bx
    return 0.5 * math.sqrt(cx * cx + cy * cy + cz * cz)


def diagnose_geometry(verts, faces, tol=1e-5):
    """Pure-Python geometric analysis of mesh topology.
    Returns detailed diagnostics, health score (0..100), and health grade."""
    n_v = len(verts)
    n_f = len(faces)
    if n_v == 0 or n_f == 0:
        return {
            "verts": n_v, "faces": n_f, "grade": "CRITICAL", "health_score": 0,
            "error": "empty mesh"
        }

    # 1. Edge to face connectivity & loose vertices
    edge_faces = {}
    used_verts = set()
    degenerate_faces = 0
    total_area = 0.0

    for fi, f in enumerate(faces):
        for vi in f:
            used_verts.add(vi)
        # Check face area (fan-triangulated if polygon)
        v0 = verts[f[0]]
        f_area = 0.0
        for i in range(1, len(f) - 1):
            f_area += triangle_area(v0, verts[f[i]], verts[f[i + 1]])
        total_area += f_area
        if f_area <= 1e-9:
            degenerate_faces += 1

        # Collect edges
        for i in range(len(f)):
            a, b = f[i], f[(i + 1) % len(f)]
            e = (min(a, b), max(a, b))
            edge_faces.setdefault(e, []).append(fi)

    loose_verts = n_v - len(used_verts)

    # 2. Non-manifold edges
    non_manifold_edges = 0
    boundary_edges = 0
    manifold_edges = 0
    for e, flist in edge_faces.items():
        count = len(flist)
        if count == 1:
            boundary_edges += 1
        elif count == 2:
            manifold_edges += 1
        else:
            non_manifold_edges += 1

    # 3. Duplicate vertices check using spatial grid
    grid = {}
    cell_size = max(1e-4, tol * 2.0)
    duplicate_verts = 0
    for vi, v in enumerate(verts):
        gx = int(math.floor(v[0] / cell_size))
        gy = int(math.floor(v[1] / cell_size))
        gz = int(math.floor(v[2] / cell_size))
        matched = False
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for dz in (-1, 0, 1):
                    key = (gx + dx, gy + dy, gz + dz)
                    if key in grid:
                        for other_i in grid[key]:
                            ov = verts[other_i]
                            dist_sq = (v[0] - ov[0])**2 + (v[1] - ov[1])**2 + (v[2] - ov[2])**2
                            if dist_sq <= tol * tol:
                                duplicate_verts += 1
                                matched = True
                                break
                    if matched:
                        break
                if matched:
                    break
            if matched:
                break
        cell_key = (gx, gy, gz)
        grid.setdefault(cell_key, []).append(vi)

    # Calculate overall health score
    penalty = 0
    penalty += min(40, non_manifold_edges * 8)
    penalty += min(30, degenerate_faces * 5)
    penalty += min(20, loose_verts * 2)
    penalty += min(20, duplicate_verts * 2)
    health_score = max(0, min(100, 100 - penalty))

    if health_score >= 85 and non_manifold_edges == 0:
        grade = "HEALTHY"
    elif health_score >= 60:
        grade = "WARN"
    else:
        grade = "CRITICAL"

    return {
        "verts": n_v,
        "faces": n_f,
        "edges": len(edge_faces),
        "total_area": round(total_area, 4),
        "loose_verts": loose_verts,
        "degenerate_faces": degenerate_faces,
        "non_manifold_edges": non_manifold_edges,
        "boundary_edges": boundary_edges,
        "duplicate_verts": duplicate_verts,
        "health_score": health_score,
        "grade": grade,
        "pass": grade in ("HEALTHY", "WARN"),
    }


def inspect_source_model(model_path):
    """Diagnoses a source mesh file (.obj, .glb, .fbx)."""
    if not os.path.isfile(model_path):
        raise ValueError(f"model file not found: {model_path}")
    ext = os.path.splitext(model_path)[1].lower()
    if ext == ".obj":
        v, f = parse_obj_mesh(model_path)
        diag = diagnose_geometry(v, f)
        diag["file"] = os.path.basename(model_path)
        return diag
    elif HAVE_BLENDER:
        return diagnose_blender_file(model_path)
    else:
        # Only OBJ is read without Blender: anything else is not inspected, and says so (never a made-up 100)
        size = os.path.getsize(model_path)
        return {
            "file": os.path.basename(model_path),
            "size_bytes": size,
            "grade": "NOT INSPECTED",
            "health_score": None,
            "note": "only an OBJ source is inspected outside Blender; run the doctor step under Blender for this one",
            "pass": None,
        }


# ---------------------------------------------------------------------------------------------------------------
# Blender BMesh Engine: In-depth Diagnosis & Auto-Healing
# ---------------------------------------------------------------------------------------------------------------

def diagnose_bmesh(bm, tol=1e-5):
    """Analyzes a bmesh object for geometric defects."""
    n_v = len(bm.verts)
    n_f = len(bm.faces)
    n_e = len(bm.edges)

    loose_verts = sum(1 for v in bm.verts if not v.link_edges)
    loose_edges = sum(1 for e in bm.edges if not e.link_faces)
    non_manifold_edges = sum(1 for e in bm.edges if len(e.link_faces) > 2)
    non_manifold_verts = sum(1 for v in bm.verts if not v.is_manifold)
    degenerate_faces = sum(1 for f in bm.faces if f.calc_area() <= 1e-8)

    penalty = 0
    penalty += min(35, non_manifold_edges * 8)
    penalty += min(25, non_manifold_verts * 4)
    penalty += min(25, degenerate_faces * 5)
    penalty += min(15, loose_verts * 2)
    penalty += min(15, loose_edges * 3)

    health_score = max(0, min(100, 100 - penalty))
    grade = "HEALTHY" if (health_score >= 85 and non_manifold_edges == 0) else ("WARN" if health_score >= 60 else "CRITICAL")

    return {
        "verts": n_v,
        "edges": n_e,
        "faces": n_f,
        "loose_verts": loose_verts,
        "loose_edges": loose_edges,
        "non_manifold_edges": non_manifold_edges,
        "non_manifold_verts": non_manifold_verts,
        "degenerate_faces": degenerate_faces,
        "health_score": health_score,
        "grade": grade,
        "pass": grade != "CRITICAL",
    }


def heal_bmesh(bm, options=None):
    """Applies sequential, safe geometric healing operations to a BMesh.
    Returns: report dict with counts of healed items."""
    opts = options or {}
    report = {
        "removed_loose_verts": 0,
        "removed_loose_edges": 0,
        "removed_degenerate_faces": 0,
        "welded_doubles": 0,
        "recalculated_normals": False,
        "healed": True,
    }

    # 1. Delete loose floating vertices and edges
    loose_v = [v for v in bm.verts if not v.link_edges]
    if loose_v:
        report["removed_loose_verts"] = len(loose_v)
        bmesh.ops.delete(bm, geom=loose_v, context='VERTS')

    loose_e = [e for e in bm.edges if not e.link_faces]
    if loose_e:
        report["removed_loose_edges"] = len(loose_e)
        bmesh.ops.delete(bm, geom=loose_e, context='EDGES')

    # 2. Remove degenerate / zero-area faces
    degen_f = [f for f in bm.faces if f.calc_area() <= 1e-8]
    if degen_f:
        report["removed_degenerate_faces"] = len(degen_f)
        bmesh.ops.delete(bm, geom=degen_f, context='FACES')

    # 3. Dissolve degenerate edges / geometry if any
    try:
        bmesh.ops.dissolve_degenerate(bm, dist=opts.get("degenerate_dist", 1e-5), edges=bm.edges)
    except Exception:
        pass

    # 4. Remove doubles (weld coincident vertices)
    dist = opts.get("weld_dist", 1e-5)
    res_doubles = bmesh.ops.remove_doubles(bm, verts=bm.verts, dist=dist)
    report["welded_doubles"] = len(res_doubles.get("verts", []))

    # 5. Recalculate consistent outward normals
    try:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
        report["recalculated_normals"] = True
    except Exception:
        pass

    return report


def heal_mesh_object(obj, options=None):
    """Heals a Blender mesh object in place and returns before/after diagnostics."""
    if not HAVE_BLENDER or obj.type != 'MESH':
        return {"error": "not a blender mesh"}

    bm = bmesh.new()
    bm.from_mesh(obj.data)

    before = diagnose_bmesh(bm)
    actions = heal_bmesh(bm, options=options)
    after = diagnose_bmesh(bm)

    bm.to_mesh(obj.data)
    bm.free()
    obj.data.update()

    return {
        "before": before,
        "actions": actions,
        "after": after,
        "improved": after["health_score"] >= before["health_score"],
    }


def diagnose_blender_file(filepath):
    """Loads a mesh into a fresh Blender scene, diagnoses it, and clears scene."""
    if not HAVE_BLENDER:
        return {"error": "Blender not available"}

    bpy.ops.wm.read_factory_settings(use_empty=True)
    ext = os.path.splitext(filepath)[1].lower()
    if ext == ".obj":
        bpy.ops.wm.obj_import(filepath=filepath)
    elif ext in (".glb", ".gltf"):
        bpy.ops.import_scene.gltf(filepath=filepath)
    elif ext == ".fbx":
        bpy.ops.import_scene.fbx(filepath=filepath)
    else:
        return {"error": f"unsupported format {ext}"}

    mesh_objs = [o for o in bpy.data.objects if o.type == 'MESH']
    if not mesh_objs:
        return {"error": "no mesh objects found"}

    main_obj = max(mesh_objs, key=lambda o: len(o.data.vertices))
    bm = bmesh.new()
    bm.from_mesh(main_obj.data)
    diag = diagnose_bmesh(bm)
    diag["file"] = os.path.basename(filepath)
    bm.free()
    return diag
