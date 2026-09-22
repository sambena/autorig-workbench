# SPDX-License-Identifier: GPL-3.0-or-later
# Autorig Workbench: stacks rerig.py's bend-test pictures into labelled sheets:  python qa_sheets.py <qa dir> <out dir> [per sheet]   (needs Pillow)
# Each picture: top row = the new skeleton at rest (side, top, front quarter; spine blue, legs green, tail orange,
# head parts purple, others grey); bottom row = the bend test (both sides and a quarter view).
import json, os, sys, glob
from PIL import Image, ImageDraw, ImageFont

src, out = sys.argv[1], sys.argv[2]
per = int(sys.argv[3]) if len(sys.argv) > 3 else 2
os.makedirs(out, exist_ok=True)
try: font = ImageFont.truetype("arial.ttf", 22)
except Exception: font = ImageFont.load_default()
rows = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(src, "*.json")))]
rows = [r for r in rows if os.path.exists(os.path.join(src, r["model"] + ".png"))]
for i in range(0, len(rows), per):
    chunk = rows[i:i + per]
    ims = [Image.open(os.path.join(src, r["model"] + ".png")).convert("RGB") for r in chunk]
    sheet = Image.new("RGB", (max(im.width for im in ims), sum(im.height for im in ims)), "white")
    y = 0
    for r, im in zip(chunk, ims):
        sheet.paste(im, (0, y))
        d = ImageDraw.Draw(sheet)
        label = "%s   bones=%s (deform %s)  unreached=%s  rigid pieces=%s  rest shift=%s" % (
            r["model"], r.get("bones"), r.get("deform_bones"), r.get("unreached_after_proxy", r.get("unreached")), r.get("rigid_pieces", "-"), r.get("rest_shift"))
        d.rectangle([0, y, sheet.width, y + 28], fill=(30, 30, 30)); d.text((6, y + 2), label, fill="white", font=font)
        y += im.height
    name = "sheet_%02d.png" % (i // per)
    sheet.save(os.path.join(out, name))
    print(name, [r["model"] for r in chunk])
