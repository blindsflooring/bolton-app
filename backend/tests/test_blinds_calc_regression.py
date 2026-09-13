# -*- coding: utf-8 -*-
"""Blinds calculator — pricing regression suite.

The ten cases below are the ones that came with the standalone
calculator, carried over verbatim (names, inputs and expected figures),
and re-run against Bolton's own ported engine. Every expected value was
cross-checked against the TBS/Luminos price list PDF during the
standalone's development; nothing here was recomputed from the port,
which would only prove the port agrees with itself.

The standalone ran these through JSDOM against the real HTML. Bolton's
engine has no DOM, so they run straight against calc_blind_line() --
still the REAL calculation path, the same function the preview endpoint
and the add-line endpoint both call, not a re-implementation of the
maths.

Each case builds a fresh blind dict. The standalone's own header warns
that reusing UI widgets across cases leaked option state and produced
two false failures; a fresh dict per case makes that class of bug
impossible here rather than merely unlikely.

Run:  python -m tests.test_blinds_calc_regression      (from backend/)
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from blinds_calc import (calc_blind_line, calc_junction_line,  # noqa: E402
                         junction_options_for, calc_pelmet_line,
                         calc_part_line, parts_catalog_for)

CASES = [
    dict(name="25mm Venetian, Contract, 1420x1850 (rounds to 1500x1900)",
         blindType="venetian25", group="CON", width=1420, drop=1850, expectBook=1174),
    dict(name="25mm Venetian, Contract, 900x1500 (exact bracket)",
         blindType="venetian25", group="CON", width=900, drop=1500, expectBook=678),
    dict(name="25mm Venetian, Contract, 1420x1850 + wooden extras",
         blindType="venetian25", group="CON", width=1420, drop=1850, wooden=True,
         expectBook=1174 + 288),
    dict(name="25mm Venetian, Contract, 1420x1850 + 1 extra colour (+25%)",
         blindType="venetian25", group="CON", width=1420, drop=1850, extraColours=1,
         expectBook=1174 * 1.25),
    dict(name="25mm Venetian, Contract, 1420x1850 + 2 extra colours (+25%, +10%)",
         blindType="venetian25", group="CON", width=1420, drop=1850, extraColours=2,
         expectBook=1174 + 1174 * 0.25 + 1174 * 0.10),
    dict(name="50mm Wood Venetian, 1000x1500 (rounds to 1050x1500)",
         blindType="wood_venetian", width=1000, drop=1500, expectBook=2180),
    dict(name="50mm Wood Venetian, 1200x1800 (exact bracket)",
         blindType="wood_venetian", width=1200, drop=1800, expectBook=2632),
    dict(name="Roller, Group 1, 1200x2000 (rounds to 1200x2100)",
         blindType="roller", group="Gr1", width=1200, drop=2000, expectBook=1336),
    dict(name="Roller, Group 1, 1200x2000 + steel chain (+51 at width 1200)",
         blindType="roller", group="Gr1", width=1200, drop=2000, steelChain=True,
         expectBook=1336 + 51),
    dict(name="Roller, Group 1, 2800x2000 without tube — must WARN, not silently price wrong",
         blindType="roller", group="Gr1", width=2800, drop=2000, expectWarning=True),
]


# Junction brackets (confirmed Sept 2026). Expected figures read off the
# price list's own bracket table, not recomputed from the port: N blinds
# meet at N-1 junctions, charged at the bracket's listed price for that
# tube size. The last two are rule cases, not price cases -- the price
# list states outright that an intermediate double bracket carries
# exactly two blinds, and a bracket that cannot carry the run must be
# refused rather than priced.
JUNCTION_CASES = [
    dict(name="Double bracket, 45mm, 2 blinds (1 junction @ 525)",
         junction="double_bracket", size="45", blinds=2, expect=525),
    dict(name="Double bracket, 38mm, 4 blinds (3 junctions @ 220)",
         junction="double_bracket", size="38", blinds=4, expect=660),
    dict(name="Coupler double bracket, 38mm, 3 blinds (2 junctions @ 825)",
         junction="coupler_double_bracket", size="38", blinds=3, expect=1650),
    dict(name="Intermediate double bracket, 45mm, 2 blinds (1 junction @ 1000)",
         junction="intermediate_double_bracket", size="45", blinds=2, expect=1000),
    dict(name="Intermediate double bracket, 3 blinds — must REFUSE, not price a chain it can't carry",
         junction="intermediate_double_bracket", size="45", blinds=3, expectError=True),
    dict(name="One blind is not a junction — must REFUSE",
         junction="double_bracket", size="38", blinds=1, expectError=True),
    dict(name="A size with no listed price is not a price of zero — must REFUSE",
         junction="double_bracket", size="55", blinds=2, expectError=True),
]


def run_junctions():
    passed = failed = 0
    for case in JUNCTION_CASES:
        got = calc_junction_line(case["junction"], case["size"], case["blinds"])
        if case.get("expectError"):
            ok = "error" in got
            detail = got.get("error", "(no error raised — it priced something)")
        elif "error" in got:
            ok, detail = False, "ERROR: " + got["error"]
        else:
            ok = abs(got["total"] - case["expect"]) < 0.005
            detail = f"expected R{case['expect']:.2f}, got R{got['total']:.2f}"
        print(("  PASS  " if ok else "  FAIL  ") + case["name"])
        if not ok or case.get("expectError"):
            print("          " + detail)
        passed += ok
        failed += not ok

    # The count rule also has to hold in the list the CARD is built from,
    # not only in the refusal at save time.
    offered_at_2 = junction_options_for(2)
    offered_at_3 = junction_options_for(3)
    ok = ("intermediate_double_bracket" in offered_at_2
          and "intermediate_double_bracket" not in offered_at_3
          and "coupler_double_bracket" in offered_at_3)
    print(("  PASS  " if ok else "  FAIL  ")
          + "the intermediate bracket is offered at 2 blinds and gone at 3")
    passed += ok
    failed += not ok

    print(f"\n{passed}/{len(JUNCTION_CASES) + 1} passed, {failed} failed")
    return failed


# Pelmets (confirmed Sept 2026). Every figure below is the price list's
# own arithmetic done by hand -- metres x the profile rate, plus the
# bracket count for that width tier at R12 a face-fix bracket, plus R60
# a mitred return -- not a number read back out of the port.
PELMET_CASES = [
    dict(name="Standard Profile, 2400mm, face fix, 2 mitres (600 + 6x12 + 2x60)",
         profile="standard90", width=2400, fixing="facefix", mitres=2,
         colour="Charcoal", expect=600 + 72 + 120),
    dict(name="Standard Profile, 2400mm, recess — brackets counted, NOT charged",
         profile="standard90", width=2400, fixing="recess", mitres=0,
         colour="Charcoal", expect=600),
    dict(name="25mm Venetian Profile, 1000mm, face fix (100 + 4x12)",
         profile="ven25", width=1000, fixing="facefix", mitres=0,
         colour="White", expect=100 + 48),
    dict(name="Victorian, 1500mm, recess, 1 mitre (735 + 60)",
         profile="victorian", width=1500, fixing="recess", mitres=1,
         colour="Teak", expect=735 + 60),
    dict(name="Over the 2700mm single length — priced, with a JOIN warning",
         profile="std50", width=3000, fixing="recess", mitres=0, colour="Sand",
         expect=450, expectWarning="join"),
    dict(name="No colour chosen — priced, but says so",
         profile="std50", width=1000, fixing="recess", mitres=0, colour="",
         expect=150, expectWarning="colour"),
    dict(name="Three mitred returns is not a thing — must REFUSE",
         profile="std50", width=1000, fixing="recess", mitres=3, colour="Sand",
         expectError=True),
    dict(name="No width — must REFUSE, not price a zero-metre pelmet",
         profile="std50", width=0, fixing="recess", mitres=0, colour="Sand",
         expectError=True),
]


def run_pelmets():
    passed = failed = 0
    for case in PELMET_CASES:
        got = calc_pelmet_line(case["profile"], case["width"], case["fixing"],
                               case["mitres"], case["colour"])
        if case.get("expectError"):
            ok = "error" in got
            detail = got.get("error", "(no error raised — it priced something)")
        elif "error" in got:
            ok, detail = False, "ERROR: " + got["error"]
        else:
            ok = abs(got["total"] - case["expect"]) < 0.005
            detail = f"expected R{case['expect']:.2f}, got R{got['total']:.2f}"
            if case.get("expectWarning"):
                warned = any(case["expectWarning"] in w.lower() for w in got["warnings"])
                ok = ok and warned
                detail += " | warnings: " + (
                    "; ".join(got["warnings"]) if got["warnings"] else "(none raised)")
        print(("  PASS  " if ok else "  FAIL  ") + case["name"])
        if not ok or case.get("expectWarning") or case.get("expectError"):
            print("          " + detail)
        passed += ok
        failed += not ok

    print(f"\n{passed}/{len(PELMET_CASES)} passed, {failed} failed")
    return failed


# Parts catalogue (confirmed Sept 2026). Prices are the list's own, and
# the three cases that refuse or warn are the ones that matter: a part
# priced per tube size must not be quoted without one, and a repair
# quoted at labour-only must say so out loud.
PART_CASES = [
    dict(name="Chain operating, plastic, 5 metres @ R7.50", part="chain_plastic",
         qty=5, expect=37.50),
    dict(name="Double bracket & cover set, 45mm, ×2 (380 each)",
         part="double_bracket_cover", size="45", qty=2, expect=760),
    dict(name="Roller coupler set, 38mm (250)", part="roller_coupler_set",
         size="38", qty=1, expect=250),
    dict(name="Repair: change roller mechanism, R110 labour + R350 mechanism",
         part="change_mechanism", qty=1, manual=350, expect=460),
    dict(name="Same repair with no mechanism price — priced at labour, and SAYS so",
         part="change_mechanism", qty=1, expect=110, expectWarning="labour"),
    dict(name="A size-priced part with no size — must REFUSE",
         part="double_bracket_cover", qty=1, expectError=True),
    dict(name="A size that isn't listed — must REFUSE, not fall back to another",
         part="double_bracket_cover", size="55", qty=1, expectError=True),
    dict(name="Quantity zero is not a line — must REFUSE",
         part="chain_plastic", qty=0, expectError=True),
]


def run_parts():
    passed = failed = 0
    for case in PART_CASES:
        got = calc_part_line(case["part"], case.get("size"), case.get("qty", 1),
                             case.get("manual"))
        if case.get("expectError"):
            ok = "error" in got
            detail = got.get("error", "(no error raised — it priced something)")
        elif "error" in got:
            ok, detail = False, "ERROR: " + got["error"]
        else:
            ok = abs(got["total"] - case["expect"]) < 0.005
            detail = f"expected R{case['expect']:.2f}, got R{got['total']:.2f}"
            if case.get("expectWarning"):
                warned = any(case["expectWarning"] in w.lower() for w in got["warnings"])
                ok = ok and warned
                detail += " | warnings: " + (
                    "; ".join(got["warnings"]) if got["warnings"] else "(none raised)")
        print(("  PASS  " if ok else "  FAIL  ") + case["name"])
        if not ok or case.get("expectWarning") or case.get("expectError"):
            print("          " + detail)
        passed += ok
        failed += not ok

    # The whole list crossed, and the per-blind-type filter still filters.
    everything = parts_catalog_for()
    roller = parts_catalog_for("roller")
    ok = (len(everything) == 104 and len(roller) == 30
          and all("roller" in i["blind_types"] for i in roller))
    print(("  PASS  " if ok else "  FAIL  ")
          + f"all 104 SKUs present, and the roller filter returns only roller parts")
    if not ok:
        print(f"          {len(everything)} total, {len(roller)} roller")
    passed += ok
    failed += not ok

    print(f"\n{passed}/{len(PART_CASES) + 1} passed, {failed} failed")
    return failed


def run():
    passed = failed = 0
    for case in CASES:
        blind = {
            "group": case.get("group"),
            "width": case["width"],
            "drop": case["drop"],
            "wooden": case.get("wooden", False),
            "extraColours": case.get("extraColours", 0),
            "steelChain": case.get("steelChain", False),
            "motor": case.get("motor", False),
            "tube55": case.get("tube55", False),
            "springAssist": case.get("springAssist", False),
        }
        got = calc_blind_line(case["blindType"], blind)

        if case.get("expectWarning"):
            ok = bool(got.get("warnings"))
            detail = (got.get("warnings") or ["(no warning raised)"])[0]
        elif "error" in got:
            ok, detail = False, "ERROR: " + got["error"]
        else:
            ok = abs(got["total"] - case["expectBook"]) < 0.005
            detail = f"expected R{case['expectBook']:.2f}, got R{got['total']:.2f}"

        print(("  PASS  " if ok else "  FAIL  ") + case["name"])
        if not ok or case.get("expectWarning"):
            print("          " + detail)
        passed += ok
        failed += not ok

    print(f"\n{passed}/{len(CASES)} passed, {failed} failed")
    return failed


if __name__ == "__main__":
    blinds_failed = run()
    print("\n--- Junction brackets ---")
    junctions_failed = run_junctions()
    print("\n--- Pelmets ---")
    pelmets_failed = run_pelmets()
    print("\n--- Parts catalogue ---")
    parts_failed = run_parts()
    sys.exit(1 if (blinds_failed or junctions_failed or pelmets_failed or parts_failed) else 0)
