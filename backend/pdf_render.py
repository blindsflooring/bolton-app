"""Dropbox Document Archive & Backup Layer brief (confirmed Aug 2026)
— the one place that turns already-built HTML into a real PDF file.

xhtml2pdf, not WeasyPrint: WeasyPrint was tried first (it renders CSS
far more faithfully, including flexbox) but fails to even import
without native Pango/Cairo/GDK-Pixbuf system libraries — confirmed
directly by installing and running it locally (a genuine
"cannot load library 'libgobject-2.0-0'" failure), which is exactly
the class of dependency a standard Render Python buildpack (no custom
Dockerfile, nothing in this repo installs system packages) will not
have either. xhtml2pdf is pure Python — reportlab under the hood, no
native dependencies — confirmed working locally, safe for this
deployment.

Known, accepted limitation: xhtml2pdf has limited/no flexbox support.
The HTML passed in is the SAME content buildPrintDocHtml() (shared.js)
already produces for the on-screen Document Preview — same text, same
line items, same amounts, same structure — so nothing about the real
DATA can ever drift between what's shown on screen and what's
archived. The visual LAYOUT of a couple of two-column areas (the
letterhead header, the totals block) may render simpler/more stacked
in the archived PDF than in a browser, since those use
display:flex — a real, disclosed limitation, not a data-fidelity
gap."""
import io
import re
from xhtml2pdf import pisa


def _resolve_css_variables(css: str) -> str:
    """xhtml2pdf's color parser has no concept of CSS custom properties
    at all -- var(--navy) crashes it outright with `ValueError: Invalid
    color value '<css function: var(--navy)>'`, raised directly from
    pisa.CreatePDF() itself (found immediately after fixing the
    @keyframes/@media print crash, re-verifying Save against a real
    Invoice again -- a second, separate real bug in the same "xhtml2pdf
    can't handle modern CSS" family, not a regression from that fix).

    Since xhtml2pdf has no notion of custom properties to begin with,
    resolve every var(...) reference to its literal value before handing
    the CSS to pisa: parse every `--name: value;` declared inside any
    :root { ... } block, then replace every var(--name) / var(--name,
    fallback) occurrence with the resolved value -- falling back to the
    fallback argument (per the CSS spec) when a name has no :root
    definition, e.g. --ink-soft in the real stylesheet, which is only
    ever referenced with a fallback and never actually declared."""
    variables = {}
    idx = 0
    while True:
        m = re.search(r":root\s*{", css[idx:])
        if not m:
            break
        block_start = idx + m.end()
        depth = 1
        j = block_start
        while j < len(css) and depth > 0:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        block_content = css[block_start:j - 1]
        for name, value in re.findall(r"(--[a-zA-Z0-9-]+)\s*:\s*([^;]+);", block_content):
            variables[name.strip()] = value.strip()
        idx = j

    def _replace_var(match):
        name = match.group(1)
        fallback = match.group(2)
        if name in variables:
            return variables[name]
        if fallback is not None:
            return fallback.strip()
        return match.group(0)  # nothing to resolve to -- leave as-is rather than guess

    return re.sub(r"var\(\s*(--[a-zA-Z0-9-]+)\s*(?:,\s*([^)]+))?\)", _replace_var, css)


def _strip_at_rule_blocks(css: str, at_rule: str) -> str:
    """Removes every `@{at_rule} ... { ... }` block from css, brace-
    depth aware — an @keyframes/@media block nests a SECOND level of {}
    inside its own outer one (e.g. `@keyframes spin { to { transform:
    rotate(360deg); } }`, or `@media print { body { display: none; } }`),
    so a naive non-nested regex would stop at the first inner `}` and
    leave a dangling extra `}` behind, corrupting the rest of the
    stylesheet instead of cleanly removing the block. `at_rule` may
    include a qualifier (e.g. "media print") to only strip that
    specific variant, not every @media block."""
    marker = f"@{at_rule}"
    result = []
    i = 0
    while True:
        idx = css.find(marker, i)
        if idx == -1:
            result.append(css[i:])
            break
        result.append(css[i:idx])
        brace_start = css.find("{", idx)
        if brace_start == -1:
            result.append(css[idx:])
            break
        depth = 1
        j = brace_start + 1
        while j < len(css) and depth > 0:
            if css[j] == "{":
                depth += 1
            elif css[j] == "}":
                depth -= 1
            j += 1
        i = j   # skip past the whole matched block, including its closing brace
    return "".join(result)


def render_html_to_pdf(html: str, css: str = "") -> bytes:
    """html/css: exactly what the frontend's buildPrintDocHtml() (or
    equivalent) already produced for on-screen viewing — this function
    does no content generation of its own, only the html-to-PDF-bytes
    conversion. Raises ValueError with pisa's own error log on failure
    rather than returning a silently-corrupt/empty PDF — the caller
    (main.py) is responsible for turning that into the correct
    DocumentArchive failure state, never a bare 500 with no reason.

    Real production bug, confirmed Aug 2026, Document Action Bar brief
    (found while verifying Save on a real Invoice — every REAL archive
    call sends the app's actual styles.css, unlike this session's own
    earlier test scripts which mostly used trivial/empty CSS strings
    and never hit this): styles.css's one loading-spinner @keyframes
    rule, AND separately its @media print block (a `:not(#printArea)`
    selector xhtml2pdf's parser also can't handle), both crashed
    xhtml2pdf's CSS parser outright — exceptions raised directly from
    pisa.CreatePDF() itself, which bypassed this function's own "raise
    ValueError, never a silent crash" contract entirely (result.err is
    never reached when CreatePDF() itself raises). This means every
    real Quote/Invoice/Order Sheet archive/save this session shipped
    has been silently 500ing in production whenever it sent the real
    stylesheet — reproduced directly, not guessed, before writing this
    fix. Both are stripped below: @keyframes because animations are
    meaningless for a static PDF; @media print because that whole block
    is entirely about the LIVE APP's own browser-print mechanism
    (hiding everything except its own #printArea element) — the
    archived document is a completely separate, self-contained
    <html>...</html> with no #printArea or any of the live app's other
    elements in it at all, so the block can never have applied to it
    either way. The real .print-doc styling that actually matters for
    layout is defined separately, outside that block, and is
    untouched. Both are lossless transforms for archival purposes, not
    real content loss — and the CreatePDF() call itself is now wrapped
    so ANY future unsupported CSS construct fails the same clean,
    catchable way instead of crashing unhandled."""
    css = _strip_at_rule_blocks(css, "keyframes")
    css = _strip_at_rule_blocks(css, "media print")
    css = _resolve_css_variables(css)
    full_html = f"<html><head><style>{css}</style></head><body>{html}</body></html>"
    buffer = io.BytesIO()
    try:
        result = pisa.CreatePDF(full_html, dest=buffer)
    except Exception as e:
        raise ValueError(f"PDF rendering failed: {type(e).__name__}: {e}")
    if result.err:
        raise ValueError(f"PDF rendering failed ({result.err} error(s))")
    pdf_bytes = buffer.getvalue()
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("PDF rendering produced no valid output")
    return pdf_bytes
