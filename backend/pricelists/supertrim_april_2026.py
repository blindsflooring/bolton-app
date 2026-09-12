# -*- coding: utf-8 -*-
"""Supertrim (Pty) Ltd — April 2026 trade price list, as printed.

Pages 1-5 come from parse_supertrim2.py reading the PDF's own text with
column positions, then checked row by row against the rendered pages.
Page 6 and the Rail/Clip block are transcribed from the rendered pages
directly: page 6 uses a different header (prices cover a GROUP of
finishes rather than one each) which the page-1-5 parser is not built
for, and transcribing four rows is safer than teaching it a second
layout for one page.

Every price is ex VAT, per length, exactly as printed. No trade discount
anywhere: Supertrim sell at list.
"""

SUPPLIER = "Supertrim"
ORDER_EMAIL = "orders@supertrim.co.za"
MIN_ORDER_EX_VAT = 3500.00          # Cape Town delivery
ISSUE = "April 2026"
SOURCE = "supertrim-trade-pricing-aluminium-2026-04"

PC = "Powder Coated "
AN = "Anodized "
FINISHES_6 = [PC + "White", PC + "Charcoal", PC + "Grey",
              AN + "Silver", AN + "Champagne", AN + "Black"]

# code, name, category, dimensions, length_m, [6 prices in FINISHES_6 order]
# None = N/a (not available in that finish)
PAGES_1_5 = [
    # --- Equal angle / corner protector (p1) ---
    ("S1010", "Aluminium Equal Angle", "angle", "10x10x2700mm", 2.7, [85, 85, 85, 85, 85, 85]),
    ("S1515", "Aluminium corner protector", "angle", "15x15x2700mm", 2.7, [99, 99, 99, 99, 99, 99]),
    ("S2020", "Aluminium Equal Angle", "angle", "20x20x2700mm", 2.7, [135, 135, 135, 135, 135, 135]),
    ("S2525", "Aluminium Equal Angle", "angle", "25x25x2700mm", 2.7, [159, 159, 159, 159, 159, 159]),
    ("S3030", "Aluminium Equal Angle", "angle", "30x30x2700mm", 2.7, [199, 199, 199, 199, 199, 199]),
    ("S4040", "Aluminium Equal Angle", "angle", "40x40x2700mm", 2.7, [219, 219, 219, 219, 219, 219]),
    ("S5050", "Aluminium Equal Angle", "angle", "50x50x2700mm", 2.7, [305, 305, 305, 305, 305, 305]),
    # --- Unequal angle / end cap (p2) ---
    ("S202", "Aluminium end cap/unequal angle", "end_cap", "6.5x15x2700mm", 2.7, [115, 115, 115, 115, 115, 115]),
    ("S201", "Aluminium end cap/unequal angle", "end_cap", "10x20x2700mm", 2.7, [139, 139, 139, 139, 139, 139]),
    ("S2515", "Aluminium unequal angle", "angle", "15x25x2700mm", 2.7, [149, 149, 149, 149, 149, 149]),
    ("S3020", "Aluminium unequal angle", "angle", "20x30x2700mm", 2.7, [159, 159, 159, 159, 159, 159]),
    ("S4030", "Aluminium unequal angle", "angle", "30x40x2700mm", 2.7, [215, 215, 215, 215, 215, 215]),
    ("S635", "Aluminium end cap", "end_cap", "6.5x35x2700mm", 2.7, [169, 169, 169, 169, 169, 169]),
    ("S1035", "Aluminium end cap", "end_cap", "10x35x2700mm", 2.7, [179, 179, 179, 179, 179, 179]),
    ("S1235", "Aluminium end cap", "end_cap", "12x35x2700mm", 2.7, [189, 189, 189, 189, 189, 189]),
    ("S1535", "Aluminium end cap", "end_cap", "15x35x2700mm", 2.7, [199, 199, 199, 199, 199, 199]),
    # --- Transitions / reducers / covers (p3) ---
    ("S120", "Aluminium reducer", "reducer", "3.5x20x2700mm", 2.7, [125, 125, 125, 125, 125, 125]),
    ("S130", "Aluminium reducer", "reducer", "4.3x30x2700mm", 2.7, [159, 159, 159, 159, 159, 159]),
    ("S128g", "Aluminium reducer", "reducer", "5.7x40x2700mm", 2.7, [199, 199, 199, 199, 199, 199]),
    ("S128g Short", "Aluminium reducer, short length", "reducer", "5.7x40x900mm", 0.9,
     [None, None, None, 75, 75, None]),
    ("S129", "Aluminium reducer", "reducer", "5.7x40x2700mm", 2.7, [225, 225, 225, 225, 225, 225]),
    ("S065", "Aluminium reducer", "reducer", "8x51x2700mm", 2.7, [None, None, None, 245, 245, 245]),
    ("S070", "Aluminium reducer", "reducer", "9.9x70x2700mm", 2.7, [None, None, None, 369, 369, 369]),
    ("S12", "Aluminium Micro T/Mould", "reducer", "4x12x2700mm", 2.7, [None, None, None, 89, 89, 89]),
    ("S133", "Aluminium Flat Cover Strip", "cover_strip", "2x24x2700mm", 2.7, [149, 149, 149, 149, 149, 149]),
    ("S068g", "Aluminium Multicover", "cover_strip", "5x40x2700mm", 2.7, [199, 199, 199, 199, 199, 199]),
    # --- Stair noses (p4-5) ---
    ("S287", "Aluminium 3mm Vinyl Stair nose, closed sided steps", "stair_nose", "23x37x2700mm", 2.7,
     [None, None, None, 195, 195, 195]),
    ("S297", "Aluminium 3mm Vinyl Stair nose, open sided steps", "stair_nose", "16.5x45x2700mm", 2.7,
     [None, None, None, 195, 195, 195]),
    ("S231", "Aluminium SPC Stair Nose, reversible 4.5-6mm floor", "stair_nose", "13.5x53x2700mm", 2.7,
     [None, None, None, 235, 235, 235]),
    ("S299", "Aluminium Stair Nose, reversible 8-10mm floor", "stair_nose", "21.5x53x2700mm", 2.7,
     [None, None, None, 255, 255, 255]),
    ("S1125", "Aluminium Stair Nose, grooved top", "stair_nose", "11x25x2700mm", 2.7,
     [None, None, None, 149, 149, None]),
    ("SS293", "Aluminium Stair Nose, grooved top", "stair_nose", "25x40x2700mm", 2.7,
     [None, None, None, 289, 289, None]),
    ("SS217", "Aluminium Stair Nose, deep grooved top", "stair_nose", "22.5x44x2700mm", 2.7,
     [None, None, None, 289, 289, None]),
    ("SS807", "Aluminium Stair Nose, 4 antislip grip", "stair_nose", "25x75x2700mm", 2.7,
     [None, None, None, 785, 785, None]),
    ("SS811", "Aluminium Stair Nose, 2 antislip grip & fluorescent strip", "stair_nose", "21x77x2700mm", 2.7,
     [None, None, None, 729, 729, None]),
    ("SS5520", "Aluminium Stair Nose, single rubber black insert", "stair_nose", "20x55x2700mm", 2.7,
     [None, None, None, 335, 335, None]),
    ("SS8520", "Aluminium Stair Nose, double rubber black insert", "stair_nose", "20x85x2700mm", 2.7,
     [None, None, None, 415, 415, None]),
    # --- Carpet profiles (p5) ---
    ("SC1006", "Aluminium Carpet Varistrip, smooth with teeth", "carpet_strip", "2700mm", 2.7,
     [None, 195, 195, 195, 195, 195]),
    ("SC1007", "Aluminium Carpet Naplok, smooth with teeth", "carpet_strip", "2700mm", 2.7,
     [None, 149, 149, 149, 149, 149]),
    ("SC6625", "Aluminium Rounded Edge, no teeth", "carpet_strip", "2700mm", 2.7,
     [115, 115, 115, 115, 115, 115]),
]

# Page 6 — its own header: one price covers a GROUP of finishes, and
# there are two families that appear nowhere else (Brushed, Mill).
# Expanded here into one row per individual finish so every product in
# the book has the same shape: profile + finish + length + price.
TPC = "Textured Powder Coated "
BR = "Brushed "
PAGE_6 = [
    ("ST323", "Aluminium 3mm Formable Edge", "tile_edge", "3x23x2500mm", 2.5,
     {TPC + "Beige": None, TPC + "White": None, TPC + "Grey": None, TPC + "Dark Grey": None,
      AN + "Silver": 109, AN + "Champagne": 109, AN + "Black": 109,
      BR + "Silver": None, BR + "Black": None, BR + "Gold": None}),
    ("ST623", "Aluminium 6mm Formable Edge", "tile_edge", "6x23x2500mm", 2.5,
     {TPC + "Beige": None, TPC + "White": None, TPC + "Grey": None, TPC + "Dark Grey": None,
      AN + "Silver": 119, AN + "Champagne": 119, AN + "Black": 119,
      BR + "Silver": None, BR + "Black": None, BR + "Gold": None}),
    ("ST1023", "Aluminium 10mm Straight Edge", "tile_edge", "10x23x2500mm", 2.5,
     {TPC + "Beige": 135, TPC + "White": 135, TPC + "Grey": 135, TPC + "Dark Grey": 135,
      AN + "Silver": 135, AN + "Champagne": 135, AN + "Black": 135,
      BR + "Silver": 135, BR + "Black": 135, BR + "Gold": 135}),
    ("ST1223", "Aluminium 12mm Straight Edge", "tile_edge", "12.5x23x2500mm", 2.5,
     {TPC + "Beige": 145, TPC + "White": 145, TPC + "Grey": 145, TPC + "Dark Grey": 145,
      AN + "Silver": 145, AN + "Champagne": 145, AN + "Black": 145,
      BR + "Silver": 145, BR + "Black": 145, BR + "Gold": 145}),
    ("CT323", "Aluminium 3mm Straight Edge (Contractor range)", "tile_edge", "3x23x2500mm", 2.5,
     {"Mill": 35}),
]

# Rail & Clip system (p5). The Clip is priced PER BAG of 28, one bag per
# length — a different unit again, recorded as its own length_m of 1 so
# the per-"length" price is the bag price and nothing pretends it is
# 2.7m of extrusion.
RAIL_CLIP = [
    ("ST-RAIL", "Supertrim Rail", "rail", "2700mm", 2.7, {"Mill": 40}),
    ("ST-CLIP", "Supertrim Clip (28 per bag, 1 bag per length)", "clip", "bag of 28", 1.0,
     {"Black": 30, "White": 30}),
]

# Stated on the price list, carried as a note rather than a product:
# SS5520 & SS8520 insert options in Grey, Light Brown, Red & Yellow are
# a R75.00 surcharge per 2.7m length ex VAT, over the standard black.
INSERT_SURCHARGE = {"applies_to": ["SS5520", "SS8520"], "amount_ex_vat": 75.00,
                    "colours": ["Grey", "Light Brown", "Red", "Yellow"]}


def rows():
    """Every product/finish combination that has a printed price."""
    out = []
    for code, name, cat, dims, length, prices in PAGES_1_5:
        for finish, price in zip(FINISHES_6, prices):
            if price is not None:
                out.append(dict(code=code, name=name, category=cat, dimensions=dims,
                                length_m=length, finish=finish, price=float(price)))
    for code, name, cat, dims, length, pmap in PAGE_6 + RAIL_CLIP:
        for finish, price in pmap.items():
            if price is not None:
                out.append(dict(code=code, name=name, category=cat, dimensions=dims,
                                length_m=length, finish=finish, price=float(price)))
    return out


if __name__ == "__main__":
    r = rows()
    profiles = sorted({x['code'] for x in r})
    print(f"{len(r)} product/finish rows across {len(profiles)} profiles")
    from collections import Counter
    for cat, n in Counter(x['category'] for x in r).most_common():
        print(f"  {cat:14} {n}")
