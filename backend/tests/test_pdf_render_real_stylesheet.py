# -*- coding: utf-8 -*-
"""Can this app's OWN stylesheet still be turned into a PDF?

Run:  python backend/tests/test_pdf_render_real_stylesheet.py

WHY THIS EXISTS, and why it is this shape. Document archiving has now been
broken four separate times by the same thing: somebody adds an ordinary line
to styles.css for the live app, xhtml2pdf's CSS parser cannot read it, and
pisa.CreatePDF() raises before a single document is rendered. Every Quote,
Invoice and Order Sheet save fails at once, and none of them ever reaches
Dropbox. The four:

    @keyframes                     the loading spinner
    @media                         a phone-only `tr:not(.oi-collapsed) > td`
    :has(input:checked)            the calendar highlight
    content: '> '                  Ask Bolton's disclosure arrows  <- this one

Each was found the same way: a real save failing in production, days or weeks
after the rule shipped. Nothing ever asked the question at build time.

THE PREVIOUS TESTS COULD NOT HAVE CAUGHT ANY OF THEM, and that is the point
of this file. pdf_render.py's own docstring records it: the earlier test
scripts "mostly used trivial/empty CSS strings and never hit this". A render
test that invents its own tidy stylesheet tests the renderer against CSS
nobody ships. This one renders against frontend/styles.css itself, whatever
is in it today — so the next unsupported construct fails here, on the
machine of whoever added it, instead of silently in production.

It deliberately does NOT assert anything about how the PDF LOOKS. xhtml2pdf's
layout limitations are known, accepted and documented. The question is only
whether a real document still becomes a real PDF.
"""

import os
import sys

BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
FE = os.path.join(os.path.dirname(BE), "frontend")
sys.path.insert(0, BE)
os.chdir(BE)

from pdf_render import render_html_to_pdf, _strip_non_ascii_in_css_strings   # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(label + ((" -- " + detail) if detail else ""))


def renders(html, css):
    try:
        pdf = render_html_to_pdf(html, css)
        return (pdf.startswith(b"%PDF"), "%d bytes" % len(pdf))
    except Exception as exc:                # noqa: BLE001
        return (False, "%s: %s" % (type(exc).__name__, str(exc)[:110]))


# A document with the things a real one has: an Afrikaans client name, an em
# dash, Rand amounts with the spaces this business writes them with.
DOC = """
<div class="print-doc">
  <div class="doc-header"><h1>Quotation</h1><div>Q-291</div></div>
  <p>Client: H&uuml;is Lettie Th&eacute;r&ocirc;n &mdash; Voëlklip</p>
  <table><tr><td>Vinyl, Aspen Herringbone 2mm</td><td class="num">R74 081,88</td></tr></table>
  <div class="totals"><div class="row"><span>Total incl VAT</span><span>R85 194,16</span></div></div>
</div>
"""

print()
print("=" * 72)
print("A. THE REAL STYLESHEET, AS IT IS TODAY")
print("=" * 72)

with open(os.path.join(FE, "styles.css"), encoding="utf-8") as fh:
    real_css = fh.read()
print("  frontend/styles.css : %d bytes" % len(real_css))

ok, detail = renders(DOC, real_css)
check("a real quote renders to a real PDF against the real stylesheet", ok, detail)
if not ok:
    print()
    print("  This is the failure mode that breaks ALL document archiving at once.")
    print("  Something in styles.css cannot be parsed by xhtml2pdf. Find it by")
    print("  bisecting the file, then close the CLASS in pdf_render.py rather")
    print("  than editing the app's stylesheet to suit the PDF renderer.")

for label, doc in [("an invoice", DOC.replace("Quotation", "Tax Invoice")),
                   ("an order sheet", '<div class="print-doc"><h2>Supertrim order</h2>'
                                      '<table><tr><td>Trim</td><td>R1 200,00</td></tr></table></div>'),
                   ("a job card", '<div class="print-doc"><h2>Job Card</h2>'
                                  '<p>J-0019 &mdash; no pricing on this one</p></div>')]:
    ok, detail = renders(doc, real_css)
    check("%s renders too" % label, ok, detail)

print()
print("=" * 72)
print("B. THE CONSTRUCTS THAT HAVE ACTUALLY BROKEN THIS, ONE BY ONE")
print("=" * 72)
print("  Each of these is a real rule that really shipped and really took")
print("  document archiving down. They must all survive the strip passes.")
print()

REGRESSIONS = [
    ("@keyframes (the loading spinner)",
     "@keyframes spin { from { transform: rotate(0deg); } to { transform: rotate(360deg); } }"),
    ("@media print (hides all but #printArea)",
     "@media print { body > *:not(#printArea) { display: none; } }"),
    ("@media max-width with :not() (phone Order Index)",
     "@media (max-width: 640px) { tr:not(.oi-collapsed) > td { display: block; } }"),
    (":has() (the calendar highlight)",
     ".cal-day:has(input:checked) { background: #eef; }"),
    ("content with a non-ASCII glyph and a space (Ask Bolton)",
     ".ask-details summary::before { content: '\u203a '; }"),
    ("the [open] variant of the same",
     ".ask-details[open] summary::before { content: '\u2304 '; }"),
    ("a non-ASCII font-family, same tokenizer fault",
     ".a { font-family: '\u25b6 x'; }"),
    ("an escaped unicode glyph, which does not help either",
     ".a::before { content: '\\25B6 '; }"),
]
for label, css in REGRESSIONS:
    ok, detail = renders(DOC, css)
    check(label, ok, detail)

print()
print("=" * 72)
print("C. THE STRIP IS NARROW -- it must not quietly eat real styling")
print("=" * 72)
print()

kept = _strip_non_ascii_in_css_strings(
    '.a { font-family: "Helvetica Neue", Arial; content: "Click to expand"; }')
check("ASCII strings are left exactly alone",
      '"Helvetica Neue"' in kept and '"Click to expand"' in kept, kept)

cleaned = _strip_non_ascii_in_css_strings(".a::before { content: '\u203a '; }")
check("only the glyph is removed, the declaration survives intact",
      "content: ' ';" in cleaned, cleaned)
check("and the rule still closes properly", cleaned.strip().endswith("}"), cleaned)

check("selectors with quoted attributes are untouched",
      'td[data-label="Status"]' in _strip_non_ascii_in_css_strings(
          'td[data-label="Status"]::before { content: none; }'))

print()
print("  And the document's OWN non-ASCII content is not the problem and must")
print("  never be touched -- half this client base has an accent in its name.")
print()

for label, doc in [
        ("an Afrikaans client name", '<div class="print-doc"><p>H&uuml;is Lettie Th&eacute;r&ocirc;n</p></div>'),
        ("an em dash", '<div class="print-doc"><p>Quote \u2014 291</p></div>'),
        ("a Rand amount as this business writes it", '<div class="print-doc"><p>R74 081,88</p></div>'),
        ("an emoji", '<div class="print-doc"><p>Done \u2705</p></div>')]:
    ok, detail = renders(doc, real_css)
    check("%s still renders" % label, ok, detail)

print()
print("=" * 72)
if failures:
    print("FAILURES: %d" % len(failures))
    for item in failures:
        print("  - " + item)
    print("=" * 72)
    sys.exit(1)
print("ALL CHECKS PASSED")
print()
print("The app's own stylesheet still produces a PDF. If this test ever fails,")
print("document archiving is broken for every quote, invoice and order sheet")
print("in the app -- not for one of them.")
print("=" * 72)
