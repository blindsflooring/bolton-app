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
