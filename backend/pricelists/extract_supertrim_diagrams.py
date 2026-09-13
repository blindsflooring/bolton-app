# -*- coding: utf-8 -*-
"""Pull the technical line drawing for every profile out of the Supertrim
October 2025 catalogue, plus the dimensions printed beside it.

Each catalogue page is a 2x2 grid of cells: the profile's line drawing on
the left of the cell, a spec table on the right (Product Code, Colours,
Packaging, Dimensions, Use example).

The drawings are EMBEDDED IMAGES, so they are lifted out whole at their
own resolution rather than re-rendered from a page crop -- a crop has to
guess where the cell ends, and it did guess wrong (one drawing sat left
of the page midline and came out blank, another swallowed the page
heading). An embedded image has an exact rectangle, and the profile it
belongs to is the Product Code label sitting to its right on the same
band. Nothing is inferred from geometry that the PDF states outright.

Small decorative images (the 7x7pt icons in the page header) are filtered
by display size.
"""
import io
import json
import os
import re

import pymupdf
from PIL import Image

PDF = r'C:\Users\burge\Dropbox\Blinds and Flooring Studio\Price Lists\Alco Trims\Supertrim brochure - October 2025.pdf'
FRONTEND = r'C:\Users\burge\blinds-flooring-bolton\bolton\frontend'
OUT_DIR = os.path.join(FRONTEND, 'img', 'supertrim')
META_OUT = r'C:\Users\burge\blinds-flooring-bolton\bolton\backend\pricelists\supertrim_profiles_oct2025.json'

# The catalogue uses several code families, not just S####: SS (stainless),
# SC (carpet), ST/CT (tile). An S-only pattern silently skipped the whole
# carpet and tile section.
CODE_RE = re.compile(r'^(?:S|SS|SC|ST|CT)\d{2,4}[A-Za-z]*$')
# A reducer or end cap is a long, THIN drawing -- filtering on height
# alone dropped four of them. Area plus a minimum on each side keeps the
# 7x7pt header icons out without excluding a flat profile.
MIN_W_PT, MIN_H_PT, MIN_AREA_PT = 20, 8, 300

os.makedirs(OUT_DIR, exist_ok=True)
doc = pymupdf.open(PDF)
profiles = {}

for pno in range(doc.page_count):
    page = doc[pno]
    words = page.get_text('words')

    # Every "Product Code" label, and the code beside it.
    labels = []
    for i, w in enumerate(words):
        if w[4] != 'Product' or i + 1 >= len(words) or words[i + 1][4] != 'Code':
            continue
        for cand in words[i + 2:i + 6]:
            if abs(cand[1] - w[1]) < 6 and CODE_RE.match(cand[4].strip()):
                labels.append({'code': cand[4].strip(), 'x0': w[0], 'y0': w[1], 'y1': w[3]})
                break

    # The spec rows for a cell run from its Product Code label down to the
    # next label below it (or the page bottom), and within the same half
    # of the page horizontally.
    def cell_text(lab):
        below = [l['y0'] for l in labels
                 if l['y0'] > lab['y0'] + 5 and abs(l['x0'] - lab['x0']) < 40]
        y_end = min(below) if below else page.rect.height
        got = [w for w in words
               if lab['y0'] - 4 <= w[1] < y_end and w[0] >= lab['x0'] - 4
               and w[0] < lab['x0'] + 420]
        got.sort(key=lambda w: (round(w[1]), w[0]))
        return ' '.join(w[4] for w in got)

    for lab in labels:
        text = cell_text(lab)
        dims = re.search(r'L:\s*([\d.]+)mm;\s*W:\s*([\d.]+)mm;\s*H:\s*([\d.]+)mm', text)
        use = re.search(r'Use example (.+?)(?:$)', text)
        name = re.search(re.escape(lab['code']) + r'\s*[–-]\s*(.+?)\s+Colours Available', text)
        # The catalogue draws its table rules as runs of underscores,
        # which land in the extracted words -- cut the name at the first
        # one rather than shipping "Aluminium Reducer ______".
        profiles[lab['code']] = {
            'code': lab['code'],
            'page': pno + 1,
            'name': re.split(r'\s*_{2,}', name.group(1))[0].strip() if name else '',
            'length_mm': float(dims.group(1)) if dims else None,
            'width_mm': float(dims.group(2)) if dims else None,
            'height_mm': float(dims.group(3)) if dims else None,
            'use_example': use.group(1).strip()[:160] if use else '',
        }

    # Each real drawing, matched to the label on its right-hand side.
    for img in page.get_images(full=True):
        xref = img[0]
        for rect in page.get_image_rects(xref):
            if (rect.width < MIN_W_PT or rect.height < MIN_H_PT
                    or rect.width * rect.height < MIN_AREA_PT):
                continue
            candidates = [l for l in labels if l['x0'] > rect.x1 - 5
                          and l['y0'] > rect.y0 - 120 and l['y0'] < rect.y1 + 120]
            if not candidates:
                continue
            lab = min(candidates, key=lambda l: (abs(l['y0'] - rect.y0), l['x0'] - rect.x1))
            raw = doc.extract_image(xref)
            img_obj = Image.open(io.BytesIO(raw['image']))
            # White background rather than transparency: these are line
            # drawings on white in the catalogue, and a transparent PNG
            # would disappear on a dark card.
            if img_obj.mode in ('RGBA', 'LA', 'P'):
                bg = Image.new('RGB', img_obj.size, 'white')
                conv = img_obj.convert('RGBA')
                bg.paste(conv, mask=conv.split()[-1])
                img_obj = bg
            else:
                img_obj = img_obj.convert('RGB')
            path = os.path.join(OUT_DIR, f"{lab['code']}.png")
            img_obj.save(path, optimize=True)
            profiles.setdefault(lab['code'], {'code': lab['code']})
            profiles[lab['code']].update({
                'diagram': f"img/supertrim/{lab['code']}.png",
                'diagram_px': [img_obj.width, img_obj.height],
                'diagram_bytes': os.path.getsize(path),
            })

with_dia = {k: v for k, v in profiles.items() if v.get('diagram')}
without = sorted(k for k, v in profiles.items() if not v.get('diagram'))
print(f'{len(profiles)} profiles in the catalogue, {len(with_dia)} with a drawing')
if without:
    print('  no drawing matched:', ', '.join(without))
for code, v in sorted(with_dia.items()):
    print(f"  {code:7s} p{v['page']:<3} {v['diagram_px'][0]}x{v['diagram_px'][1]}  "
          f"{v['diagram_bytes'] / 1024:5.1f} KB  "
          f"W{v.get('width_mm')} H{v.get('height_mm')}  {v.get('name', '')[:38]}")
print(f"\ntotal drawing weight: {sum(v['diagram_bytes'] for v in with_dia.values()) / 1024:.0f} KB")

io.open(META_OUT, 'w', encoding='utf-8').write(
    json.dumps({'issue': 'October 2025', 'source': os.path.basename(PDF),
                'profiles': profiles}, indent=1, sort_keys=True))
print('metadata ->', META_OUT)
