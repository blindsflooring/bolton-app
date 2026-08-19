"""
Pricing calculations — the actual proprietary IP of this app.
All formulas here are confirmed by Burgert; do not change without his sign-off.
"""
from models import (
    FlooringProduct, BlindsProduct, JobType, JOB_TYPE_MULTIPLIERS,
    StairwellType, STAIRWELL_NOSING_MM, STAIRWELL_LABOUR_PER_STAIR, TILES_PER_STAIR, STAIR_AREA_M2,
)

FLOORING_MARGIN_WARN_THRESHOLD = 0.30  # warn if a discount pushes margin below this

# Screed / smoothing compound allowance (confirmed Aug 2026, iTe LEVELiTe F10
# 20kg bags at R235 ex VAT — coverage varies by substrate, deeper fill needed
# for rougher prep):
DEFAULT_BAG_COVERAGE_M2 = {
    JobType.smooth: 4.0,
    JobType.over_tiles: 3.0,
    JobType.removed_tiles: 2.0,
}
DEFAULT_BAG_COST = 235.0            # iTe LEVELiTe F10, 20kg
BAG_OVERAGE_RATE = 350.0            # confirmed Aug 2026: R350 per bag beyond the included allowance, INCL. VAT (not ex-VAT) — used as a flat client-facing site-variance charge, shown on the printed quote, not added to VAT again
# CORRECTED Aug 2026: confirmed R45/m² is INCL VAT, not ex VAT like every
# other figure in this app. Stored here as the ex-VAT equivalent so the
# normal "VAT applied once at quote level" flow still lands on exactly
# R45 incl VAT to the client, instead of R51.75 (45 x 1.15) if it had
# been treated as ex VAT and had VAT re-applied on top.
TILE_REMOVAL_FEE_PER_M2_INCL_VAT = 45.0
TILE_REMOVAL_FEE_PER_M2 = TILE_REMOVAL_FEE_PER_M2_INCL_VAT / 1.15   # ≈ 39.13 ex VAT


def calculate_flooring_line(
    product: FlooringProduct,
    quantity_m2: float,
    job_type: JobType,
    discount_pct: float = 0.0,
    glue_cost_per_unit: float = 0.0,
    glue_coverage_m2: float = 0.0,
    labour_rate_per_m2: float = 45.0,   # confirmed Aug 2026: R45/m² is standard on ALL floors — not optional, part of the base pricing structure
    bag_cost: float = DEFAULT_BAG_COST,
    bag_coverage_m2: float = None,
    own_staff: bool = True,
    markup_override: float = None,   # confirmed Aug 2026: per-quote markup override — actually applied to the saved line, not just a live-preview-only value
    include_tile_removal_fee: bool = False,   # screed only — explicit per-line toggle, not auto-tied to job_type (a job can need tile removal billed independent of the Smooth/Over Tiles/Removed Tiles prep rate selected)
) -> dict:
    """
    Screed formula (confirmed): selling price = base rate x job-type multiplier
    (Smooth x1, Over Tiles x1.5, Removed Tiles x2), ex VAT. Only applies to
    products with pricing_type == "screed".

    Material formula (confirmed Aug 2026): vinyl/laminate/oak etc. price is
    FLAT regardless of job type — job_type is still recorded on the line for
    reference (e.g. what prep this material is going onto), but does not
    change the price. The substrate prep cost lives on a separate screed
    line item on the same quote, not baked into the material price.

    Job cost buildup (confirmed Aug 2026, Techem Tek 70/70 as first real
    example — R1,193.50 ex VAT per 20L drum, covers 70m²):
    Glue and labour are calculated on the ACTUAL job m² (quantity_m2), NOT
    the wastage-inflated purchasing quantity used for packs_needed. Confirmed
    directly: "The glue and labour gets added to the 120m2" (not 129.6m2).
    Glue is bought by the drum, rounded UP (can't buy part of a drum) — same
    rounding logic as packs_needed, just on a different area basis.
    Only applies to MATERIAL lines — screed's job-type multiplier already
    represents its own labour/complexity, so no separate glue/labour added
    there; blinds are priced as finished units, not per-m² installation jobs.
    Currently this cost is NOT added to the client-facing sell price
    (unit_price/line_total unchanged) — it only affects the margin
    calculation, since Burgert hasn't yet confirmed whether installation is
    billed to the client separately or absorbed into the material rate.
    Flag this in brief review — may need a "billed to client" toggle later.

    Screed bag allowance (confirmed Aug 2026): bag coverage per m² varies by
    substrate — Smooth 4m²/bag, Over Tiles 3m²/bag, Removed Tiles 2m²/bag
    (deeper compound fill needed on rougher prep). The bag allowance IS
    included in the quoted price (it's what the job-type multiplier is
    pricing for); it's shown on the line as a reference so both Burgert and
    the client know what's covered. If actual on-site usage exceeds this
    allowance, extra bags are billed at R350/bag (BAG_OVERAGE_RATE) — this
    is a site-variance charge, not something this quote-time calculation
    applies automatically, since it depends on what's actually used on site.
    Tile removal fee (CORRECTED Aug 2026): confirmed R45/m² INCL VAT —
    stored/calculated here as its ex-VAT equivalent so the client ends up
    paying exactly R45/m² incl VAT once the quote's VAT is applied, not
    R51.75. Controlled by an explicit include_tile_removal_fee toggle, not
    auto-applied just because job_type is Removed Tiles — a job might need
    tiles removed without wanting it billed as its own line, or vice versa.
    Billed as its own visible amount on top of the smoothing compound line
    (not folded into the ×2 multiplier).
    """
    is_screed = product.pricing_type == "screed"
    if is_screed:
        # CORRECTED Aug 2026: multipliers are per-product and editable, not
        # a fixed system-wide 1x/1.5x/2x — confirmed from your real 8-year
        # calculator that Over Tiles/Removed Tiles rates are NOT a clean
        # multiple of Smooth (e.g. deZIGN S200 screed: 130/160/250, not
        # 130/195/260 that a flat 1.5x/2x would give).
        screed_multipliers = {
            JobType.smooth: 1.0,  # always the base rate — that IS the smooth price
            JobType.over_tiles: product.over_tiles_multiplier,
            JobType.removed_tiles: product.removed_tiles_multiplier,
        }
        multiplier = screed_multipliers[job_type]
    else:
        multiplier = 1.0

    import math

    glue_units_needed = 0
    glue_cost_total = 0.0
    glue_sell_total = 0.0
    labour_cost_total = 0.0
    labour_charged_total = 0.0
    bags_allowed = 0
    compound_cost_total = 0.0
    tile_removal_fee_total = 0.0
    delivery_fee_total = 0.0
    boxes_needed = 0

    if is_screed:
        # BUG FIXED Aug 2026: screed's real cost is the compound bags used —
        # nothing else. An earlier version also computed a "material_cost_total"
        # using the same wholesale-minus-discount formula as vinyl boxes, but
        # base_cost_ex_vat for screed IS your confirmed sell rate (e.g. R130/m²
        # smooth), not a wholesale price needing a discount applied. That bug
        # was charging the ENTIRE sell price as cost, then adding the real bag
        # cost on top — a confirmed real example (ITE F10, R130/m² smooth,
        # 100m², 25 bags @ R235) came out to a fabricated -45% to -53% margin
        # instead of the correct ~55%. unit_price/line_total are unaffected —
        # only the cost side was wrong.
        unit_price = product.base_cost_ex_vat * multiplier * (1 - discount_pct)
        line_total = unit_price * quantity_m2
        material_cost_total = 0.0  # screed has no separate material cost basis beyond the compound bags below

        coverage = bag_coverage_m2 or DEFAULT_BAG_COVERAGE_M2[job_type]
        bags_allowed = math.ceil(quantity_m2 / coverage)
        compound_cost_total = bags_allowed * bag_cost
        if include_tile_removal_fee:
            tile_removal_fee_total = TILE_REMOVAL_FEE_PER_M2 * quantity_m2
            line_total += tile_removal_fee_total

        total_job_cost = compound_cost_total + tile_removal_fee_total
        unit_cost_display = total_job_cost / quantity_m2 if quantity_m2 else 0.0  # real cost per m² — bags only, no phantom material cost

    else:
        # CORRECTED Aug 2026: material sell price now follows the confirmed
        # box-by-box buildup from your real worked example, not a flat
        # base_cost x markup. Verified against your numbers exactly:
        # 100m2 job, series 200 -> 33 boxes -> R17,163.30 box cost ->
        # +R1,705 glue -> R18,868.30 subtotal -> x1.30 markup -> R24,528.79
        # -> +R4,500 labour -> R29,028.79 vinyl line ex VAT. sell_markup_multiplier
        # is now the multiplier applied to (boxes+glue), e.g. 1.30 for a
        # 30% markup — same field, reinterpreted per your confirmed formula.
        boxes_needed = math.ceil((quantity_m2 * (1 + product.wastage_pct)) / product.m2_per_pack) if product.m2_per_pack else 0
        # BUG FIXED Aug 2026: base_cost_ex_vat is documented and used
        # EVERYWHERE else in this app (screed, price book imports, CSVs) as
        # a per-m² rate — e.g. R222/m² for series 200's Zone A price. This
        # line was treating it as if it were already a per-box price,
        # multiplying box count directly against a per-m² figure with no
        # conversion. Correct conversion: per-m² rate x (1-discount) x
        # m2_per_pack = net cost per box. An earlier test of this formula
        # "passed" only because it was fed R743 (the real per-box price)
        # directly into this per-m² field — that masked the bug rather
        # than catching it. Verified against your real numbers with the
        # correct per-m² input (222): net cost/box ≈ R519.81, box total
        # ≈ R17,153.83 for 33 boxes — matching your worked example.
        net_cost_per_box = product.base_cost_ex_vat * (1 - product.trade_discount_pct) * (product.m2_per_pack or 1)
        box_total_cost = boxes_needed * net_cost_per_box
        # Settlement discount (confirmed Aug 2026, same principle as
        # BlindsProduct): a further discount on top of trade discount,
        # kept entirely as margin — deliberately NOT folded into
        # box_total_cost above, since that figure feeds subtotal ->
        # marked_up -> line_total (the actual client-facing price).
        # This only ever reduces the reported cost/margin figures.
        true_net_cost_per_box = net_cost_per_box * (1 - product.settlement_discount_pct)
        box_total_true_cost = boxes_needed * true_net_cost_per_box

        if glue_cost_per_unit and glue_coverage_m2:
            # Glue drawn from stock (confirmed Aug 2026) — cost = charge,
            # same clean per-m² rate, no drum-rounding in job costing.
            glue_units_needed = math.ceil(quantity_m2 / glue_coverage_m2)
            glue_rate_per_m2 = glue_cost_per_unit / glue_coverage_m2
            glue_cost_total = quantity_m2 * glue_rate_per_m2
            glue_sell_total = quantity_m2 * glue_rate_per_m2

        # Delivery fee (confirmed Aug 2026, e.g. Aspen — no trade discount,
        # R15/m² delivery on top). Bundled into the same pre-markup subtotal
        # as boxes+glue, matching your confirmed formula exactly ("boxes +
        # glue... now do my markup") — so you're not just recovering the
        # delivery cost, you're earning your normal margin on it too.
        # Defaults to 0 for every other supplier, so this never affects them.
        delivery_fee_total = quantity_m2 * product.delivery_fee_per_m2
        subtotal = box_total_cost + glue_cost_total + delivery_fee_total
        effective_markup = markup_override if markup_override is not None else product.sell_markup_multiplier
        marked_up = subtotal * effective_markup
        labour_charged_total = quantity_m2 * labour_rate_per_m2
        labour_cost_total = 0.0 if own_staff else labour_charged_total

        line_total = (marked_up * (1 - discount_pct)) + labour_charged_total
        unit_price = marked_up / quantity_m2 if quantity_m2 else 0.0  # informational per-m² rate, pre-labour
        material_cost_total = box_total_true_cost + glue_cost_total + delivery_fee_total
        total_job_cost = material_cost_total + labour_cost_total
        unit_cost_display = material_cost_total / quantity_m2 if quantity_m2 else 0.0

    # Both branches (screed and material) now compute their own line_total,
    # material_cost_total, and total_job_cost inline above — no shared
    # formula here, since the two are structurally different (screed:
    # all-in rate + optional tile-removal fee; material: box-by-box
    # buildup + glue + markup + labour).
    overall_margin_pct = (line_total - total_job_cost) / line_total if line_total else 0.0

    warning = None
    if overall_margin_pct < FLOORING_MARGIN_WARN_THRESHOLD:
        warning = (
            f"Overall margin on this line is {overall_margin_pct:.1%}, below the "
            f"{FLOORING_MARGIN_WARN_THRESHOLD:.0%} warning threshold."
        )

    result = {
        "unit_cost": round(unit_cost_display, 2),
        "unit_price": round(unit_price, 2),
        "line_total": round(line_total, 2),
        "margin_pct": round(overall_margin_pct, 4),
        "glue_units_needed": glue_units_needed,
        "glue_cost_total": round(glue_cost_total, 2),
        "glue_sell_total": round(glue_sell_total, 2),
        "labour_cost_total": round(labour_cost_total, 2),
        "labour_charged_total": round(labour_charged_total, 2),
        "own_staff": own_staff,
        "bags_allowed": bags_allowed,
        "compound_cost_total": round(compound_cost_total, 2),
        "tile_removal_fee_total": round(tile_removal_fee_total, 2),
        "delivery_fee_total": round(delivery_fee_total, 2),
        "total_job_cost": round(total_job_cost, 2),
        "warning": warning,
    }

    # packs_needed mirrors boxes_needed for material lines (both use the
    # same wastage-adjusted formula) — kept as a separate response key for
    # backward compatibility with anything still reading it.
    if product.m2_per_pack:
        result["packs_needed"] = math.ceil(
            (quantity_m2 * (1 + product.wastage_pct)) / product.m2_per_pack
        )

    return result


def calculate_stairwell_line(
    vinyl_product: FlooringProduct,
    nosing_product,  # TrimProduct
    num_stairs: int,
    stairwell_type: StairwellType,
    tiles_per_stair: int = TILES_PER_STAIR,
    stair_area_m2: float = STAIR_AREA_M2,
    glue_cost_per_unit: float = 1193.50,
    glue_coverage_m2: float = 70.0,
    own_staff: bool = True,
) -> dict:
    """
    Stairwell formula (confirmed Aug 2026 — two DIFFERENT area bases, for
    two different reasons, confirmed directly by Burgert):

    - VINYL is billed on a TILE-COUNT basis, not the raw stair geometry.
      2 tiles/stair (confirmed minimum) x num_stairs = total tiles needed.
      Tile area is derived from the product's actual plank dimensions
      (tiles_per_pack, confirmed exact whole numbers — e.g. deZIGN series
      200 = 12 tiles/pack). This is billed area, not geometric area, because
      tile offcuts can't be reused ("I can't do anything with the offcuts")
      — confirmed example: 10 stairs x 2 tiles = 20 tiles = 5.58m² on
      series 200, and that 5.58m² is what's actually charged, even though
      the raw stair footprint (900x300x200mm) is smaller. Boxes needed are
      rounded up from tile count; cost is for full boxes bought, sell is
      for the tile area actually used (5.58m², not the larger box-rounded
      total).
    - GLUE uses the raw GEOMETRIC stair area instead (900mm wide tread x
      (300mm going + 200mm riser) = 0.45m²/stair, confirmed default),
      because glue coverage is about the real substrate footprint, not
      tile offcuts. Confirmed: 10 stairs = 4.5m² of glue coverage needed —
      genuinely different from the 5.58m² vinyl figure above.
    - Stair nosing: 900mm/stair closed, +500mm one side open, +500+500mm
      both sides open. Confirmed default profile: S2525 Aluminium Equal
      Angle (25x25mm). Nosing cost includes the trim's own wastage_pct
      (confirmed 8% default).
    - Labour: R250/stair closed, R300/stair one side open, R350/stair both
      sides open. CORRECTED Aug 2026: this rate is always what's CHARGED to
      the client — the actual COST depends on who does the work.
      own_staff=True (default): your own salaried guys — the job doesn't
      create new labour cost (they're paid regardless), so labour cost is
      treated as R0 and the full charged amount is margin. own_staff=False:
      outside/subcontracted labour — cost is treated as pass-through
      (roughly what you actually pay out), same as before.
    - Landing is NOT part of this calculation — priced as a normal flooring
      material line at m² rate, per Burgert's confirmation.
    """
    if not vinyl_product.tiles_per_pack:
        raise ValueError("Vinyl product has no tiles_per_pack set — needed for stairwell tile-count billing")

    import math

    # Vinyl: TILE-COUNT basis, not geometric area — confirmed billed amount
    total_tiles_needed = tiles_per_stair * num_stairs
    boxes_needed = math.ceil(total_tiles_needed / vinyl_product.tiles_per_pack)
    net_cost_per_m2 = vinyl_product.base_cost_ex_vat * (1 - vinyl_product.trade_discount_pct)
    box_cost = net_cost_per_m2 * vinyl_product.m2_per_pack
    vinyl_cost_total = boxes_needed * box_cost
    tile_area_m2 = vinyl_product.m2_per_pack / vinyl_product.tiles_per_pack
    billed_vinyl_area_m2 = total_tiles_needed * tile_area_m2   # e.g. 5.58m² — confirmed billed figure
    # BUG FIXED Aug 2026: this was selling at raw base_cost_ex_vat with no
    # markup applied at all — the same sell_markup_multiplier used on
    # regular flooring lines (confirmed ×1.3 for a 30% markup) was never
    # referenced here, meaning every stair job has been underselling vinyl
    # by the full markup amount since this was built.
    vinyl_sell_total = billed_vinyl_area_m2 * vinyl_product.base_cost_ex_vat * vinyl_product.sell_markup_multiplier

    # Glue: GEOMETRIC stair area, deliberately different from vinyl's billed area
    glue_area_m2 = stair_area_m2 * num_stairs   # e.g. 4.5m² — confirmed genuinely smaller than vinyl's 5.58m²
    # CORRECTED Aug 2026: glue is drawn from stock, never bought fresh per
    # job — cost and charge are BOTH the same clean per-m² rate, no
    # drum-rounding in job costing. glue_units_needed kept purely as a
    # reference figure (how much of a drum this job represents).
    glue_units_needed = math.ceil(glue_area_m2 / glue_coverage_m2) if glue_coverage_m2 else 0
    glue_rate_per_m2 = (glue_cost_per_unit / glue_coverage_m2) if glue_coverage_m2 else 0.0
    glue_cost_total = glue_area_m2 * glue_rate_per_m2
    glue_sell_total = glue_area_m2 * glue_rate_per_m2

    # Nosing (includes the trim's own confirmed 8% wastage buffer on cost)
    nosing_mm_per_stair = STAIRWELL_NOSING_MM[stairwell_type]
    nosing_length_m = (nosing_mm_per_stair / 1000) * num_stairs
    if nosing_product.pricing_mode == "fixed":
        nosing_unit_price = nosing_product.fixed_sell_price_per_lm or 0.0
    else:
        nosing_unit_price = nosing_product.cost_ex_vat_per_lm * nosing_product.markup_multiplier
    nosing_sell_total = nosing_length_m * nosing_unit_price
    nosing_cost_total = nosing_length_m * nosing_product.cost_ex_vat_per_lm * (1 + getattr(nosing_product, "wastage_pct", 0.08))

    # Labour — confirmed pass-through, deliberately part of the overall
    # stairwell margin rather than marked up on its own
    labour_per_stair = STAIRWELL_LABOUR_PER_STAIR[stairwell_type]
    labour_charged_total = labour_per_stair * num_stairs   # always what's charged to the client
    labour_cost_total = 0.0 if own_staff else labour_charged_total   # own staff = salaried, no marginal job cost; outside = pass-through

    line_total = vinyl_sell_total + glue_sell_total + nosing_sell_total + labour_charged_total
    total_cost = vinyl_cost_total + glue_cost_total + nosing_cost_total + labour_cost_total
    margin_pct = (line_total - total_cost) / line_total if line_total else 0.0

    warning = None
    if margin_pct < FLOORING_MARGIN_WARN_THRESHOLD:
        warning = f"Overall margin on this stairwell line is {margin_pct:.1%}, below the {FLOORING_MARGIN_WARN_THRESHOLD:.0%} warning threshold."

    return {
        "billed_vinyl_area_m2": round(billed_vinyl_area_m2, 2),
        "glue_area_m2": round(glue_area_m2, 2),
        "boxes_needed": boxes_needed,
        "vinyl_cost_total": round(vinyl_cost_total, 2),
        "vinyl_sell_total": round(vinyl_sell_total, 2),
        "glue_units_needed": glue_units_needed,
        "glue_cost_total": round(glue_cost_total, 2),
        "glue_sell_total": round(glue_sell_total, 2),
        "nosing_length_m": round(nosing_length_m, 2),
        "nosing_cost_total": round(nosing_cost_total, 2),
        "nosing_sell_total": round(nosing_sell_total, 2),
        "labour_charged_total": round(labour_charged_total, 2),
        "labour_cost_total": round(labour_cost_total, 2),
        "own_staff": own_staff,
        "line_total": round(line_total, 2),
        "total_job_cost": round(total_cost, 2),
        "margin_pct": round(margin_pct, 4),
        "warning": warning,
    }


def calculate_trim_line(product, length_m: float, discount_pct: float = 0.0) -> dict:
    """
    Trim/skirting formula (CORRECTED Aug 2026 — VAT architecture fixed):
    - "fixed" mode (pine skirting): sell price is the flat R/lm figure set
      directly — Burgert's own "final installed price", e.g. 69mm=R80/lm.
      Not derived from cost via any formula.
    - "markup" mode (aluminium trims): sell price = cost x markup_multiplier
      (e.g. x1.5). An earlier draft baked VAT into this (cost x (1+VAT) x
      markup), following an initial instruction to "use trim book price
      plus vat then add 50%". CONFIRMED CORRECTED Aug 2026: VAT must be
      stripped out of every internal calculation across the whole app and
      applied exactly ONCE, at final invoice time — never baked into any
      line's unit_price. This matches how material, blinds, and screed
      already worked; trim was the one inconsistent spot. vat_pct is kept
      on the product record for Phase 2 (mapping to the correct Xero tax
      rate on the invoice line), not used in this price calculation.
    - Wastage (confirmed Aug 2026): 8% extra length bought for offcuts/
      mitres — affects COST only (you buy 8% more than the job needs).
      The client is charged for the actual length required, not the extra.
    """
    if product.pricing_mode == "fixed":
        unit_price = product.fixed_sell_price_per_lm or 0.0
    else:
        unit_price = product.cost_ex_vat_per_lm * product.markup_multiplier

    unit_price *= (1 - discount_pct)
    line_total = unit_price * length_m
    line_cost = product.cost_ex_vat_per_lm * length_m * (1 + product.wastage_pct)
    margin_pct = (line_total - line_cost) / line_total if line_total else 0.0

    warning = None
    if margin_pct < FLOORING_MARGIN_WARN_THRESHOLD:
        warning = f"Margin on this trim line is {margin_pct:.1%}, below the {FLOORING_MARGIN_WARN_THRESHOLD:.0%} warning threshold."

    return {
        "unit_cost": round(product.cost_ex_vat_per_lm, 2),
        "unit_price": round(unit_price, 2),
        "line_total": round(line_total, 2),
        "margin_pct": round(margin_pct, 4),
        "warning": warning,
    }


def calculate_blinds_line(
    product: BlindsProduct,
    width_mm: float,
    drop_mm: float,
    discount_pct: float = 0.0,
) -> dict:
    """
    Blinds formula (confirmed, ~49% margin): net cost = book price less 45%
    trade discount, less further 7.5% settlement discount. "Sell at book"
    means the EX-VAT selling price IS the book price — VAT is a separate
    line-item tax added on top at invoice time (Xero's job via tax rate),
    NOT baked into the price used for margin. This was verified against
    Burgert's confirmed ~49% margin figure:
        cost = 1000 * 0.55 * 0.925 = 508.75
        margin = (1000 - 508.75) / 1000 = 49.125%  -- matches
    (An earlier draft incorrectly used book*1.15 as the selling price for
    margin purposes, which gives 55.8% margin — wrong. Fixed here.)

    vat_pct is kept on the product record for Phase 2 (mapping to the
    correct Xero tax rate on the invoice line), not used in this calc.

    Discounts up to 10-15% off selling price still leave ~40-43% margin
    (more headroom than flooring), so no warning threshold applied here by
    default — flag in brief §16 for review if you want one added.

    NOTE: this assumes book_price is already sized to the width/drop band on
    the product record (per the width_band/drop_band fields). If bands need
    interpolation between sizes, that's a Phase 1.5 refinement — flag if so.
    """
    net_cost = product.book_price * (1 - product.trade_discount_pct)
    net_cost *= (1 - product.settlement_discount_pct)

    unit_price_ex_vat = product.book_price * (1 - discount_pct)

    margin_pct = (unit_price_ex_vat - net_cost) / unit_price_ex_vat if unit_price_ex_vat else 0.0

    return {
        "unit_cost": round(net_cost, 2),
        "unit_price": round(unit_price_ex_vat, 2),
        "line_total": round(unit_price_ex_vat, 2),  # blinds priced per unit, qty=1 per line
        "unit_price_incl_vat": round(unit_price_ex_vat * (1 + product.vat_pct), 2),
        "margin_pct": round(margin_pct, 4),
        "warning": None,
    }


def line_real_cost(line) -> float:
    """Shared cost-basis helper — used by both the quote's "at a glance"
    margin check and the commission engine, so the two can never diverge
    (a divergence here is exactly the kind of bug found and fixed
    repeatedly earlier in this project — e.g. the screed cost bug, the
    stairwell missing-markup bug). Do not reimplement this inline
    elsewhere; import and call this instead."""
    if line.category in ("flooring", "stairwell"):
        return line.total_job_cost
    if line.category == "trim":
        return line.unit_cost * (line.length_m or 1)
    return line.unit_cost  # blinds — priced per unit, qty=1
