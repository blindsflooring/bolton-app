# -*- coding: utf-8 -*-
"""TBS/Luminos blinds pricing engine (confirmed Sept 2026, "Integrate
standalone Blinds Calculator into Bolton").

This is a port of the standalone calculator's engine
(blinds_quote_calculator_v19.html), not a re-derivation of it. Two things
were done deliberately to keep that true:

1. The pricing data was NOT retyped. The DATA object literal was read out
   of the HTML and serialised to JSON by Node, so all 4,214 numbers moved
   across mechanically -- pricelists/blinds_tbs_luminos_v19.json is that
   file. A transcription error is the single most likely way a port like
   this goes wrong, and the only real defence is not transcribing.

2. The logic is a line-for-line port of the same six pieces: findBracket,
   lookupPrice, lookupAddon, and the three option shapes
   (optPriceByWidth, optPercentSurchargeTiered, optFlatAddon), driven by
   the same declarative product registry. Same structure, so the two can
   be read side by side.

WHY PYTHON AND NOT THE BROWSER. The calculator computes a sell price,
and in Bolton a price that reaches a quote is always computed server-side
-- it is the same reason cost and margin are stripped in
strip_sensitive_fields() rather than hidden with CSS. A browser-computed
price posted to an endpoint is a price anybody can choose. The frontend
still gets a live preview, from this same code over an endpoint, so there
is exactly one implementation of the maths and no second one to drift.

The imported-spreadsheet path is untouched and stays the source of truth
for sheets; this covers the "quote it by hand" half that until now had
only the price-book product lookup behind it.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

_DATA_PATH = os.path.join(os.path.dirname(__file__), "pricelists",
                          "blinds_tbs_luminos_v19.json")

with open(_DATA_PATH, encoding="utf-8") as _f:
    DATA: Dict[str, Any] = json.load(_f)

# Labels the standalone calculator showed beside a group code. Kept here
# rather than in the frontend for the same reason SUPERTRIM_TERMS is:
# they are facts about the supplier's price list, and a label hardcoded
# into a screen is the one nobody finds when the list is reissued.
GROUP_LABELS = {
    "venetian25": {"CON": "Contract", "Gr1": "Group 1", "Gr2": "Group 2"},
    "roller": {"Gr1": "Group 1", "Gr2": "Group 2", "Gr3": "Group 3", "Gr4": "Group 4"},
}


def find_bracket(sorted_values: List[float], target: float) -> Optional[float]:
    """The first bracket at or above the measurement.

    Blinds are priced by size BAND, not by the millimetre: a 1420mm wide
    blind is charged at the 1500mm column. Rounding down here would
    undercharge every non-exact size in the book, which is why this walks
    up and returns the first value >= target rather than the nearest.
    """
    for v in sorted_values:
        if v >= target:
            return v
    return None


def lookup_price(grid: Dict[str, Any], width: float, drop: float) -> Dict[str, Any]:
    w = find_bracket(grid["widths"], width)
    if w is None:
        return {"error": f"Width {width:g}mm exceeds max in table ({grid['widths'][-1]:g}mm) — POA"}
    drop_keys = sorted(int(k) for k in grid["rows"])
    d = find_bracket(drop_keys, drop)
    if d is None:
        return {"error": f"Drop {drop:g}mm exceeds max in table ({drop_keys[-1]:g}mm) — POA"}
    idx = grid["widths"].index(w)
    return {"price": grid["rows"][str(d)][idx], "used_width": w, "used_drop": d}


def lookup_addon(table: Dict[str, Any], width: float) -> Optional[float]:
    w = find_bracket(table["widths"], width)
    if w is None:
        return None
    return table["prices"][table["widths"].index(w)]


def is_spring_assist_recommended(group: str, width: float, drop: float) -> bool:
    """Recommended, never applied automatically -- the standalone
    calculator treats this as a prompt to the person quoting, and a
    surcharge that appears without being chosen is how a quote grows a
    line nobody can explain to the client."""
    thresholds = DATA["roller"]["spring_assist"]["thresholds"].get(group)
    if not thresholds or not width or not drop:
        return False
    for t in thresholds:
        if drop <= t["maxDrop"]:
            return width >= t["minWidth"]
    return False


# ---------- the three option shapes, ported as-is ----------

def _opt_price_by_width(table_path: List[str], label: str):
    def apply(ctx):
        table = DATA
        for k in table_path:
            table = table[k]
        p = lookup_addon(table, ctx["width"])
        if p is None:
            return None
        return {"label": label, "val": p, "sub": True}
    return apply


def _opt_percent_surcharge_tiered(count_key: str, first_pct: float, rest_pct: float, label_fn):
    """First occurrence at one rate, each further at a lower rate, every
    one off the BASE price -- not compounded on the running total. That
    distinction is the whole reason this shape exists: two extra colours
    is base + 25% + 10% of base, not base x 1.25 x 1.10."""
    def apply(ctx):
        count = ctx["blind"].get(count_key) or 0
        if count <= 0:
            return None
        surcharge = 0.0
        for k in range(1, int(count) + 1):
            surcharge += ctx["base_price"] * (first_pct if k == 1 else rest_pct)
        return {"label": label_fn(int(count)), "val": surcharge, "sub": True}
    return apply


def _opt_flat_addon(price_path: List[str], label: str):
    def apply(ctx):
        price = DATA
        for k in price_path:
            price = price[k]
        if price is None:
            return None
        return {"label": label, "val": price, "sub": True}
    return apply


# ---------- product registry, mirroring PRODUCTS in the standalone ----------

PRODUCTS: Dict[str, Dict[str, Any]] = {
    "venetian25": {
        "label": "25mm Aluminium Venetian",
        "supplier": "tbs_luminos",
        "groups": ["CON", "Gr1", "Gr2"],
        "grid_for": lambda b: DATA["venetian25"][b.get("group", "CON")],
        "group_label": lambda b: GROUP_LABELS["venetian25"].get(b.get("group")),
        "options": [
            (lambda b: bool(b.get("wooden")),
             _opt_price_by_width(["venetian25", "wooden_extras"], "Wooden extras")),
            (lambda b: (b.get("extraColours") or 0) > 0,
             _opt_percent_surcharge_tiered("extraColours", 0.25, 0.10,
                                           lambda n: f"Additional colour surcharge ({n})")),
        ],
        "rules": [],
    },
    "wood_venetian": {
        "label": "50mm Wood Venetian",
        "supplier": "tbs_luminos",
        "groups": [],
        "grid_for": lambda b: DATA["wood_venetian"],
        "group_label": lambda b: None,
        "options": [],
        "rules": [],
    },
    "plascon_wood": {
        "label": "50mm Plascon Wood",
        "supplier": "tbs_luminos",
        "groups": [],
        "grid_for": lambda b: DATA["plascon_wood"],
        "group_label": lambda b: None,
        "options": [],
        "rules": [],
    },
    "roller": {
        "label": "Roller Blind",
        "supplier": "tbs_luminos",
        "groups": ["Gr1", "Gr2", "Gr3", "Gr4"],
        "grid_for": lambda b: DATA["roller"][b.get("group", "Gr1")],
        "group_label": lambda b: GROUP_LABELS["roller"].get(b.get("group")),
        "options": [
            (lambda b: bool(b.get("steelChain")),
             _opt_price_by_width(["roller", "steel_chain"], "Steel chain upgrade")),
            (lambda b: bool(b.get("motor")),
             _opt_price_by_width(["roller", "motor_add"], "Motorisation add")),
            (lambda b: bool(b.get("tube55")),
             _opt_price_by_width(["roller", "tube55_add"], "55mm tube")),
            (lambda b: bool(b.get("springAssist")),
             _opt_flat_addon(["roller", "spring_assist", "price"], "Spring assist")),
        ],
        "rules": [
            (lambda ctx: ctx["width"] >= 2700 and not ctx["blind"].get("tube55"),
             lambda ctx: "width is 2700mm+ — 55mm tube is required per spec."),
        ],
    },
}


def price_label(used_width: float, used_drop: float, group_label: Optional[str]) -> str:
    bits = f"Book price ({used_width:g} x {used_drop:g}"
    if group_label:
        bits += f", {group_label}"
    return bits + ")"


def calc_blind_line(product_key: str, blind: Dict[str, Any]) -> Dict[str, Any]:
    """One blind, priced. Returns {rows, total, warnings} or {error}.

    Same contract as the standalone's calcBlindLine(product, b, tag): the
    breakdown rows are returned as well as the total, because a blinds
    price is a book price plus named add-ons and a quote that shows only
    the total cannot be checked against the supplier's own list.
    """
    product = PRODUCTS.get(product_key)
    if product is None:
        return {"error": f"Unknown blind type: {product_key}"}
    try:
        width = float(blind.get("width") or 0)
        drop = float(blind.get("drop") or 0)
    except (TypeError, ValueError):
        return {"error": "Width and drop must be numbers."}
    if width <= 0 or drop <= 0:
        return {"error": "Enter a width and a drop."}

    grid = product["grid_for"](blind)
    lookup = lookup_price(grid, width, drop)
    if "error" in lookup:
        return {"error": lookup["error"] + ". Contact factory for a custom quote."}

    rows = [{"label": price_label(lookup["used_width"], lookup["used_drop"],
                                  product["group_label"](blind)),
             "val": lookup["price"], "sub": False}]
    total = float(lookup["price"])

    ctx = {"width": width, "drop": drop, "base_price": float(lookup["price"]), "blind": blind}
    for enabled, apply in product["options"]:
        if not enabled(blind):
            continue
        result = apply(ctx)
        if result:
            rows.append(result)
            total += result["val"]

    warnings = [msg(ctx) for when, msg in product["rules"] if when(ctx)]

    return {
        "rows": rows,
        "total": round(total, 2),
        "warnings": warnings,
        "used_width": lookup["used_width"],
        "used_drop": lookup["used_drop"],
        "product_label": product["label"],
        "group_label": product["group_label"](blind),
        "spring_assist_recommended": (
            product_key == "roller"
            and not blind.get("springAssist")
            and is_spring_assist_recommended(blind.get("group", "Gr1"), width, drop)
        ),
    }


# ---------- Junction brackets (confirmed Sept 2026, "confirm scope, bring
# in remaining blind types") ----------
#
# Window-level in the standalone, not per-blind: what a junction costs
# depends on how many blinds share one reveal, not on any single blind's
# own options, which is why it sits outside calc_blind_line() there and
# here. Bolton has no "window" object -- one line is one blind -- so a
# junction is quoted as its own line that names how many blinds it joins.
#
# Roller only. The standalone's own note: roller is the only blind type
# with documented junction/coupler bracket pricing, and the others show a
# note rather than a fabricated number.
JUNCTION_LABELS = {
    "none": "None (independent blinds)",
    "double_bracket": "Double bracket — shared bracket, independent chains",
    "intermediate_double_bracket": "Intermediate double bracket — mid-span support (2 blinds only)",
    "coupler_double_bracket": "Coupler double bracket — one control chain (chains in series)",
}


def junction_sizes(junction_type: str) -> List[str]:
    """The tube sizes this bracket is actually listed at, read off the
    price list rather than assumed to be the same for all three."""
    table = DATA["roller"]["brackets"].get(junction_type) or {}
    return sorted(table, key=lambda s: int(s))


def junction_options_for(blind_count: int) -> List[str]:
    """Which junction types are legal for this many blinds.

    A port of junctionOptionsFor(): the maxBlinds rule is the price
    list's own ("mid-span support only -- limited to exactly 2 blinds,
    not chainable in series"), so an intermediate double bracket
    disappears from the list at three blinds rather than being offered
    and then silently mispricing a chain it cannot carry.
    """
    out = []
    for key in JUNCTION_LABELS:
        rule = DATA["roller"]["junction_rules"].get(key)
        if not rule or rule.get("maxBlinds") is None:
            out.append(key)
        elif blind_count <= rule["maxBlinds"]:
            out.append(key)
    return out


def calc_junction_line(junction_type: str, size: str, blind_count: int) -> Dict[str, Any]:
    """One junction run, priced. Returns {rows, total, warnings} or {error}.

    N blinds in a reveal meet at N-1 junctions -- the standalone's own
    arithmetic (`const junctions = r.count - 1`), and the reason this
    line asks for the number of BLINDS rather than the number of
    brackets: the person quoting is looking at the window, not counting
    the joins between them.
    """
    if junction_type == "none" or junction_type not in DATA["roller"]["brackets"]:
        return {"error": f"Unknown junction bracket: {junction_type}"}
    try:
        count = int(blind_count)
    except (TypeError, ValueError):
        return {"error": "Number of blinds must be a whole number."}
    if count < 2:
        return {"error": "A junction bracket joins two or more blinds — enter at least 2."}

    rule = DATA["roller"]["junction_rules"].get(junction_type) or {}
    max_blinds = rule.get("maxBlinds")
    if max_blinds is not None and count > max_blinds:
        # Refused, not priced: this is the one junction rule the supplier
        # states outright, and quoting a chain the bracket cannot carry
        # is a fitting problem on site, not a pricing one.
        return {"error": f"{JUNCTION_LABELS[junction_type]} supports at most {max_blinds} blinds. "
                         + (rule.get("note") or "")}

    size_key = str(size)
    table = DATA["roller"]["brackets"][junction_type]
    if size_key not in table:
        return {"error": f"No {size_key}mm price listed for this bracket "
                         f"(listed: {', '.join(junction_sizes(junction_type))}mm)."}

    junctions = count - 1
    unit = table[size_key]
    label = f"{JUNCTION_LABELS[junction_type]} ({size_key}mm) × {junctions}"
    return {
        "rows": [{"label": label, "val": unit * junctions, "sub": False}],
        "total": round(float(unit * junctions), 2),
        "warnings": [],
        "junctions": junctions,
        "unit_price": unit,
        "product_label": JUNCTION_LABELS[junction_type].split(" — ")[0],
        "group_label": f"{size_key}mm, {count} blinds",
    }


# ---------- Pelmets (confirmed Sept 2026, "confirm scope, bring in
# remaining blind types") ----------
#
# A port of calcPelmetFor(). Window-level in the standalone for the same
# reason a junction is -- a pelmet spans the reveal, not a blind -- so in
# Bolton it is its own line.
#
# Priced by the metre off the profile, plus brackets by a width tier,
# plus mitred returns. Two things carried across deliberately because
# they are the price list's own position rather than an oversight:
# recess brackets are counted but NOT charged ("not separately priced in
# source list"), and a pelmet over the maximum single length is a join,
# not a refusal.
PELMET_MAX_MITRE_SIDES = 2


def pelmet_profiles() -> List[Dict[str, Any]]:
    return [{"key": k, "label": v["label"], "price_per_m": v["pricePerM"],
             "width_mm": v["widthMM"]}
            for k, v in DATA["pelmet"]["profiles"].items()]


def pelmet_colours() -> List[str]:
    return list(DATA["pelmet"]["colours"])


def _pelmet_bracket_tier(fixing: str, width: float) -> Optional[Dict[str, Any]]:
    """The bracket count for this width. Tiers are inclusive ranges off
    the price list; a width past the last tier has no listed count, and
    the standalone adds no bracket row at all rather than extrapolating
    one -- kept, because inventing a bracket count is inventing a price."""
    for tier in DATA["pelmet"]["brackets"].get(fixing, []):
        if tier["from"] <= width <= tier["to"]:
            return tier
    return None


def calc_pelmet_line(profile_key: str, width_mm: float, fixing: str = "recess",
                     mitre_sides: int = 0, colour: str = "") -> Dict[str, Any]:
    """One pelmet, priced. Returns {rows, total, warnings} or {error}."""
    profile = DATA["pelmet"]["profiles"].get(profile_key)
    if profile is None:
        return {"error": f"Unknown pelmet profile: {profile_key}"}
    if fixing not in DATA["pelmet"]["brackets"]:
        return {"error": f"Unknown pelmet fixing: {fixing}"}
    try:
        width = float(width_mm or 0)
        sides = int(mitre_sides or 0)
    except (TypeError, ValueError):
        return {"error": "Pelmet width and mitre count must be numbers."}
    if width <= 0:
        return {"error": "Enter the pelmet width."}
    if sides < 0 or sides > PELMET_MAX_MITRE_SIDES:
        return {"error": f"A pelmet has at most {PELMET_MAX_MITRE_SIDES} mitred returns."}

    rows: List[Dict[str, Any]] = []
    warnings: List[str] = []
    length_m = width / 1000.0
    profile_cost = profile["pricePerM"] * length_m
    colour_text = f", {colour}" if colour else ""
    rows.append({"label": f"Pelmet: {profile['label']}{colour_text} "
                          f"({length_m:.2f}m @ R{profile['pricePerM']}/m)",
                 "val": profile_cost, "sub": False})
    total = profile_cost

    if not colour:
        warnings.append("Pelmet colour not chosen yet.")
    if width > DATA["pelmet"]["max_length_mm"]:
        warnings.append(f"Pelmet width {width:g}mm exceeds the maximum single length "
                        f"({DATA['pelmet']['max_length_mm']}mm) — will need a join.")

    tier = _pelmet_bracket_tier(fixing, width)
    if tier:
        if fixing == "facefix":
            price = DATA["pelmet"]["facefix_bracket_price"]
            rows.append({"label": f"Pelmet face fix brackets ({tier['qty']} × R{price})",
                         "val": tier["qty"] * price, "sub": True})
            total += tier["qty"] * price
        else:
            # Counted, not charged -- the source list prices no recess
            # bracket, and a zero row that says so is more useful on a
            # quote than a silently missing one.
            rows.append({"label": f"Pelmet recess brackets ({tier['qty']} required)",
                         "val": 0, "sub": True,
                         "note": "not separately priced in source list"})

    if sides > 0:
        mitre = DATA["pelmet"]["mitre_price"]
        rows.append({"label": f"Pelmet mitred returns ({sides} × R{mitre})",
                     "val": sides * mitre, "sub": True})
        total += sides * mitre

    return {
        "rows": rows,
        "total": round(total, 2),
        "warnings": warnings,
        "product_label": f"Pelmet - {profile['label']}",
        "group_label": f"{width:g}mm, {'face fix' if fixing == 'facefix' else 'recess'}",
    }


# ---------- Parts catalogue (confirmed Sept 2026, "confirm scope, bring
# in remaining blind types") ----------
#
# 104 SKUs: rails, cords, tilters, chain by the metre, bracket sets and
# repair work. A port of catalogFor()/addPartToQuote()/calcPartsFor().
#
# Three shapes in one list, and the difference matters at quoting time:
#   - a plain price per unit (91 of them);
#   - variants, priced per tube size (12) -- a size is REQUIRED, because
#     the sizes are different prices, not the same part;
#   - one repair line (change roller mechanism) that is a fixed labour
#     base PLUS whatever the mechanism itself costs, which is not in any
#     list and has to be typed in.
#
# Unlike a blind, a part genuinely has a quantity: five metres of chain
# is one line. That does not reopen the qty question settled in
# add_blinds_calc_line() -- the invariant there is one BLIND per line,
# and this is not a blind. unit_cost carries the whole line's cost so
# line_real_cost() stays correct either way.


def parts_catalog_for(blind_type: Optional[str] = None) -> List[Dict[str, Any]]:
    """The catalogue, optionally filtered to what fits one blind type --
    the standalone's own filter, kept because a venetian tilter offered
    on a roller quote is a mis-order waiting to happen."""
    out = []
    for item in DATA["parts_catalog"]:
        if blind_type and blind_type not in item["blindTypes"]:
            continue
        out.append({
            "id": item["id"],
            "label": item["label"],
            "group": item["group"],
            "unit": item["unit"],
            "price": item.get("price"),
            "variants": item.get("variants"),
            "manual_price": bool(item.get("manualPrice")),
            "base": item.get("base"),
            "blind_types": item["blindTypes"],
        })
    return out


def find_part(part_id: str) -> Optional[Dict[str, Any]]:
    for item in DATA["parts_catalog"]:
        if item["id"] == part_id:
            return item
    return None


def calc_part_line(part_id: str, size: Optional[str] = None, qty: int = 1,
                   manual_price: Optional[float] = None) -> Dict[str, Any]:
    """One catalogue part, priced. Returns {rows, total, warnings} or {error}."""
    item = find_part(part_id)
    if item is None:
        return {"error": f"Unknown part: {part_id}"}
    try:
        quantity = int(qty or 0)
    except (TypeError, ValueError):
        return {"error": "Quantity must be a whole number."}
    if quantity < 1:
        return {"error": "Enter a quantity of at least 1."}

    size_key = str(size) if size not in (None, "") else None
    warnings: List[str] = []

    if item.get("variants"):
        if size_key is None:
            return {"error": f"{item['label']} is priced by size — choose one "
                             f"({', '.join(sorted(item['variants'], key=int))}mm)."}
        if size_key not in item["variants"]:
            return {"error": f"No {size_key}mm price listed for {item['label']} "
                             f"(listed: {', '.join(sorted(item['variants'], key=int))}mm)."}
        unit_price = float(item["variants"][size_key])
    elif item.get("manualPrice"):
        # The labour base is listed; the mechanism is not. Priced at the
        # base alone if nobody typed one in, and SAID so -- a repair
        # quoted at labour-only is a real mistake, not a rounding one.
        mech = float(manual_price or 0)
        unit_price = float(item["base"]) + mech
        if mech <= 0:
            warnings.append(f"No mechanism price entered — this is the R{item['base']:g} "
                            f"labour charge only.")
    else:
        unit_price = float(item["price"])
        if size_key is not None:
            warnings.append(f"{item['label']} is not priced by size — the size was ignored.")

    label = item["label"] + (f" ({size_key}mm)" if size_key and item.get("variants") else "")
    label += f" × {quantity}"
    return {
        "rows": [{"label": label, "val": unit_price * quantity, "sub": False}],
        "total": round(unit_price * quantity, 2),
        "warnings": warnings,
        "unit_price": unit_price,
        "product_label": item["label"],
        "group_label": (f"{size_key}mm, " if size_key and item.get("variants") else "")
                       + f"{quantity} {item['unit']}" + ("s" if quantity != 1 else ""),
    }
