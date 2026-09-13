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

from blinds_calc import calc_blind_line  # noqa: E402

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
    sys.exit(1 if run() else 0)
