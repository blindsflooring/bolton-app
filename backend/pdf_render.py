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
from xhtml2pdf import pisa


def render_html_to_pdf(html: str, css: str = "") -> bytes:
    """html/css: exactly what the frontend's buildPrintDocHtml() (or
    equivalent) already produced for on-screen viewing — this function
    does no content generation of its own, only the html-to-PDF-bytes
    conversion. Raises ValueError with pisa's own error log on failure
    rather than returning a silently-corrupt/empty PDF — the caller
    (main.py) is responsible for turning that into the correct
    DocumentArchive failure state, never a bare 500 with no reason."""
    full_html = f"<html><head><style>{css}</style></head><body>{html}</body></html>"
    buffer = io.BytesIO()
    result = pisa.CreatePDF(full_html, dest=buffer)
    if result.err:
        raise ValueError(f"PDF rendering failed ({result.err} error(s))")
    pdf_bytes = buffer.getvalue()
    if not pdf_bytes.startswith(b"%PDF"):
        raise ValueError("PDF rendering produced no valid output")
    return pdf_bytes
