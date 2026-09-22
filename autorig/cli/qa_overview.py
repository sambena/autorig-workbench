# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: one page of every rebuilt rig: the new skeleton at rest beside the bend test.  python qa_overview.py [qa dir]   (needs Pillow)
import json, os, sys, glob
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "core"))
import layout
src = sys.argv[1] if len(sys.argv) > 1 else layout.work_dir("qa")
try: font = ImageFont.truetype("arial.ttf", 18)
except Exception: font = ImageFont.load_default()
rows = []
for p in sorted(glob.glob(os.path.join(src, "*.json"))):
    r = json.load(open(p))
    png = os.path.join(src, r["model"] + ".png")
    if os.path.exists(png) and not r.get("error"): rows.append((r, png))
T, S, COLS = 520, 300, 3
cell_w, cell_h = S * 2, S + 26
sheet = Image.new("RGB", (cell_w * COLS, cell_h * ((len(rows) + COLS - 1) // COLS)), "white")
d = ImageDraw.Draw(sheet)
for i, (r, png) in enumerate(rows):
    im = Image.open(png).convert("RGB")
    rest = im.crop((0, 0, T, T)).resize((S, S), Image.LANCZOS)
    posed = im.crop((0, T, T, T * 2)).resize((S, S), Image.LANCZOS)
    x, y = (i % COLS) * cell_w, (i // COLS) * cell_h
    sheet.paste(rest, (x, y + 26)); sheet.paste(posed, (x + S, y + 26))
    d.rectangle([x, y, x + cell_w - 2, y + 25], fill=(30, 30, 30))
    d.text((x + 6, y + 3), "%s   %s bones, %s deform" % (r["model"], r.get("bones"), r.get("deform_bones")), fill="white", font=font)
out = os.path.join(src, "overview.png")
sheet.save(out)
print(out, len(rows), "models", sheet.size)
