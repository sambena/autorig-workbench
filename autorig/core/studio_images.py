# SPDX-License-Identifier: GPL-3.0-or-later
"""Image helpers adapted from Storm Chaser's reference, foliage and surface tools."""
import io
import json
import urllib.request
from pathlib import Path
from PIL import Image, ImageDraw
import studio


def reference_sheet(settings, model_dir, out):
    folder = Path(studio.input_folder(settings["folder"], model_dir)) if settings["folder"] else None
    rows = []
    for view in ("Front", "Side", "Back", "Face"):
        render = Image.open(out / (view + ".png")).convert("RGB")
        matches = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg") and
                         p.stem.lower().endswith(view.lower())) if folder else []
        if view == "Face" and not matches and folder:
            matches = sorted(p for p in folder.iterdir() if p.suffix.lower() in (".png", ".jpg", ".jpeg") and p.stem.lower().endswith("portrait"))
        images = []
        for im in ([Image.open(matches[0])] if matches else []) + [render]:
            im = im.convert("RGBA")
            bg = Image.new("RGBA", im.size, "white"); bg.alpha_composite(im)
            bg.thumbnail((1200, 600), Image.Resampling.LANCZOS)
            images.append(bg.convert("RGB"))
        rows.append((view + (": reference / model" if matches else ": model"), images))
    width = max(sum(im.width for im in images) + 24 * (len(images) + 1) for _, images in rows)
    heights = [max(im.height for im in images) + 60 for _, images in rows]
    sheet = Image.new("RGB", (width, sum(heights) + 24), "white")
    draw = ImageDraw.Draw(sheet); y = 24
    for (label, images), height in zip(rows, heights):
        draw.text((24, y), label, fill="black"); x = 24
        for im in images: sheet.paste(im, (x, y + 25)); x += im.width + 24
        y += height
    sheet.save(out / "comparison.png")


def foliage(settings, out):
    from studio_ports import leaves
    # The original artwork is authored at 512 px; resize whole cells together to retain gutters.
    atlas = leaves.build(seed=settings["seed"])
    cell = settings["cell"]
    atlas = atlas.resize((cell * 4, cell * 3), Image.Resampling.LANCZOS)
    atlas.save(out / "foliage.png")
    names = ["oak", "round", "bush", "pine", "litter", "twigs", "flowers", "weeds", "corn", "corn2", "grass", "grass_dry"]
    studio.atomic_json(out / "atlas.json", {"image": "foliage.png", "alpha_cutoff": 0.5, "seed": settings["seed"],
        "cells": [{"name": name, "rect": [(i % 4) * cell, (i // 4) * cell, cell, cell]} for i, name in enumerate(names)]})
    # Ready-to-import crossed cards. OBJ uses the same alpha atlas; material metadata specifies cutoff.
    lines = ["mtllib foliage.mtl"]
    for i, name in enumerate(names):
        lines += ["o " + name, "usemtl foliage"]
        u, v = (i % 4) / 4, 1 - (i // 4 + 1) / 3
        lines += ["v -0.5 0 0", "v 0.5 0 0", "v 0.5 1 0", "v -0.5 1 0",
                  "v 0 0 -0.5", "v 0 0 0.5", "v 0 1 0.5", "v 0 1 -0.5"]
        for _ in range(2):
            lines += ["vt %.6f %.6f" % p for p in ((u,v),(u+.25,v),(u+.25,v+1/3),(u,v+1/3))]
        for start in (i*8+1, i*8+5):
            lines += ["f " + " ".join("%d/%d" % (k,k) for k in range(start,start+4))]
    (out / "foliage.obj").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out / "foliage.mtl").write_text("newmtl foliage\nKd 1 1 1\nmap_Kd foliage.png\nmap_d foliage.png\n", encoding="utf-8")


MAP_KEYS = {"albedo": ["diff", "Diffuse", "diffuse", "Color", "color"],
    "normal": ["nor_gl", "normal_gl", "Normal_GL"], "roughness": ["rough", "Rough", "roughness"],
    "ao": ["ao", "AO", "ambient_occlusion"], "height": ["disp", "Displacement", "displacement", "height"]}


def fetch(url):
    if not url.startswith("https://"): raise ValueError("material download must use HTTPS")
    req = urllib.request.Request(url, headers={"User-Agent": "AutorigWorkbench/1.0"})
    with urllib.request.urlopen(req, timeout=120) as r: return r.read()


def material(settings, out):
    slug, res, packing = settings["asset"], settings["resolution"], settings["packing"]
    dest = out / (slug + "_" + res + "_" + packing); dest.mkdir(exist_ok=True)
    files = json.loads(fetch("https://api.polyhaven.com/files/" + slug))
    maps, urls = {}, {}
    for kind, keys in MAP_KEYS.items():
        url = next((files[k][res][fmt]["url"] for k in keys if k in files and res in files[k]
                    for fmt in ("png", "jpg") if fmt in files[k][res]), None)
        if not url and kind in ("albedo", "normal", "roughness"): raise ValueError("missing " + res + " " + kind)
        if url:
            cached = dest / (kind + ".png")
            if cached.exists(): im = Image.open(cached).copy()
            else:
                im = Image.open(io.BytesIO(fetch(url))).convert("RGB" if kind in ("albedo", "normal") else "L")
                im.save(cached)
            maps[kind] = im; urls[kind] = url
    size = maps["albedo"].size
    for k in ("roughness", "ao", "height"):
        maps[k] = maps[k].convert("L").resize(size) if k in maps else Image.new("L", size, 255 if k == "ao" else 128)
    channels = (maps["ao"], maps["roughness"], Image.new("L", size, 0)) if packing == "orm" else (maps["roughness"], maps["ao"], maps["height"])
    Image.merge("RGB", channels).save(dest / (packing + ".png"))
    studio.atomic_json(dest / "material.json", {"asset": slug, "provider": "Poly Haven", "source": "https://polyhaven.com/a/" + slug,
        "license": "CC0", "downloads": urls, "normal_convention": "OpenGL", "albedo_color_space": "sRGB",
        "data_color_space": "linear", "packing": packing, "channels": ["occlusion", "roughness", "metallic"] if packing == "orm" else ["roughness", "occlusion", "height"]})
