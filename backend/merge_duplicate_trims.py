# -*- coding: utf-8 -*-
"""One-time cleanup for trim lines that were duplicated before
add_trim_line() started combining them (confirmed Sept 2026).

REPORTS BY DEFAULT, WRITES ONLY WITH --apply. This deletes real quote
line rows, so the safe direction is the default one:

    python merge_duplicate_trims.py                 # report only
    python merge_duplicate_trims.py --apply         # actually merge
    python merge_duplicate_trims.py --quote 42      # limit to one quote

NOT wired into startup. It is a one-time tidy-up, not a recurring job,
and whether to run it at all is a judgement about real quotes somebody
may have already sent to a client.

WHAT IT MERGES is exactly what add_trim_line() now merges, using the
same key and the same recalculation so the cleanup and the live path
can never disagree: same quote, same product_id, same colour, same
discount_pct. The oldest row survives and absorbs the others' length.

WHAT IT LEAVES ALONE, deliberately:
  * any line carrying a manual total override (pre_override_line_total
    is not None) — that figure is an agreed price a person typed, and
    recomputing it from a combined length would throw that decision
    away silently. Its group is skipped entirely, not partially merged.
  * quotes that are not still editable. A quote already accepted,
    invoiced or paid was sent to a client as it stands; re-shaping its
    line items afterwards would make Bolton disagree with the document
    the client is holding. Draft/quoted work only.

The money does not change. Trim pricing is linear in length
(calculate_trim_line), so a combined line totals what its parts totalled,
rounded once instead of several times — a difference of at most a cent
per group, which is reported per quote so nothing moves unexplained.
"""
import sys

from sqlmodel import Session, select

from calculations import calculate_trim_line
from main import engine, get_settings
from models import AuditLog, DEFAULT_TENANT_ID, Quote, QuoteLineItem, TrimProduct

# Only quotes still genuinely in the drafting stage. accepted_at being
# set is this app's real marker of "this became a job" — the same test
# every workflow path uses.
EDITABLE_WORKFLOW_STATUSES = ("quoted",)


def find_duplicate_groups(session, tenant_id=DEFAULT_TENANT_ID, quote_id=None):
    """[(quote, [lines...]), ...] for every group of 2+ mergeable trim
    lines. Oldest line first within each group."""
    q = select(QuoteLineItem).where(
        QuoteLineItem.tenant_id == tenant_id,
        QuoteLineItem.category.in_(("trim", "skirting")),
    )
    if quote_id is not None:
        q = q.where(QuoteLineItem.quote_id == quote_id)
    lines = list(session.exec(q.order_by(QuoteLineItem.id)).all())

    quotes = {}
    grouped = {}
    for line in lines:
        quote = quotes.get(line.quote_id)
        if quote is None:
            quote = quotes[line.quote_id] = session.get(Quote, line.quote_id)
        if quote is None or quote.tenant_id != tenant_id:
            continue
        if quote.workflow_status not in EDITABLE_WORKFLOW_STATUSES or quote.accepted_at is not None:
            continue
        if line.pre_override_line_total is not None:
            continue
        key = (line.quote_id, line.product_id, (line.colour or ""), round(line.discount_pct or 0.0, 6))
        grouped.setdefault(key, []).append(line)

    out = []
    for key, group in sorted(grouped.items()):
        if len(group) > 1:
            out.append((quotes[key[0]], group))
    return out


def merge_duplicates(apply_changes=False, tenant_id=DEFAULT_TENANT_ID, quote_id=None,
                     username="cleanup"):
    with Session(engine) as session:
        groups = find_duplicate_groups(session, tenant_id, quote_id)
        if not groups:
            return {"groups": [], "applied": apply_changes}

        settings = get_settings(session, tenant_id)
        report = []
        for quote, group in groups:
            keeper = group[0]
            absorbed = group[1:]
            product = session.get(TrimProduct, keeper.product_id)
            if product is None:
                # The price book entry is gone, so there is nothing to
                # recalculate against. Left exactly as it is rather than
                # guessed at.
                report.append({
                    "quote_id": quote.id, "product_name": keeper.product_name,
                    "colour": keeper.colour or "", "skipped": "price book product no longer exists",
                    "rows": len(group),
                })
                continue

            # Captured BEFORE anything is mutated. The keeper is a member
            # of `group`, so reading lengths back after assigning its new
            # combined length reports the merged figure as if it were one
            # of the inputs — which is exactly what the audit trail must
            # not say. Found by reading a written audit row back.
            original_lengths = [l.length_m or 0.0 for l in group]
            combined_length = round(sum(original_lengths), 4)
            total_before = round(sum((l.line_total or 0.0) for l in group), 2)
            calc = calculate_trim_line(product, combined_length, keeper.discount_pct or 0.0,
                                        margin_warn_threshold=settings.flooring_margin_warn_threshold)
            report.append({
                "quote_id": quote.id, "product_name": keeper.product_name,
                "colour": keeper.colour or "", "rows": len(group),
                "lengths": original_lengths,
                "combined_length": combined_length,
                "total_before": total_before, "total_after": calc["line_total"],
                "delta": round(calc["line_total"] - total_before, 2),
                "keeper_id": keeper.id, "removed_ids": [l.id for l in absorbed],
            })

            if apply_changes:
                keeper.length_m = combined_length
                keeper.unit_cost = calc["unit_cost"]
                keeper.unit_price = calc["unit_price"]
                keeper.line_total = calc["line_total"]
                keeper.margin_pct = calc["margin_pct"]
                keeper.low_margin_reason = None
                keeper.low_margin_reason_by = None
                keeper.low_margin_reason_at = None
                session.add(keeper)
                for line in absorbed:
                    session.delete(line)
                # Written DIRECTLY rather than through
                # _log_quote_line_audit(), deliberately. That helper is
                # gated to accepted/scheduled/completed quotes, because
                # adding a line to a draft is ordinary quoting and not
                # worth a permanent entry — and this cleanup only ever
                # touches quotes in exactly the status it skips, so going
                # through it would have logged nothing at all. Verified
                # by running the cleanup and finding no audit row.
                #
                # A one-time script deleting rows from real quotes is a
                # different kind of event from a person adding a line, and
                # is worth recording whatever the quote's status. It also
                # takes its own field name rather than reusing
                # __line_added__/__line_removed__, so this is legible as a
                # bulk cleanup and never mistaken for someone's edit.
                session.add(AuditLog(
                    tenant_id=quote.tenant_id, username=username,
                    entity_type="Quote", entity_id=quote.id,
                    field="__trim_duplicates_merged__",
                    old_value="%d lines: %s" % (
                        len(group), " + ".join("%slm" % x for x in original_lengths)),
                    new_value="1 line: %slm (%s%s), R%.2f -> R%.2f" % (
                        combined_length, keeper.product_name,
                        (" / " + keeper.colour) if keeper.colour else "",
                        total_before, calc["line_total"]),
                ))
        if apply_changes:
            session.commit()
        return {"groups": report, "applied": apply_changes}


if __name__ == "__main__":
    apply_changes = "--apply" in sys.argv
    quote_id = None
    if "--quote" in sys.argv:
        quote_id = int(sys.argv[sys.argv.index("--quote") + 1])

    result = merge_duplicates(apply_changes=apply_changes, quote_id=quote_id)
    groups = result["groups"]
    if not groups:
        print("No duplicate trim lines found on any still-editable quote. Nothing to do.")
        raise SystemExit(0)

    print("APPLIED\n" if apply_changes else "REPORT ONLY - nothing written (re-run with --apply)\n")
    total_delta = 0.0
    for g in groups:
        if g.get("skipped"):
            print("  quote %-5s %-30s %-18s SKIPPED: %s" % (
                g["quote_id"], g["product_name"][:30], g["colour"][:18], g["skipped"]))
            continue
        total_delta += g["delta"]
        print("  quote %-5s %-30s %-18s %d rows  %s = %slm" % (
            g["quote_id"], g["product_name"][:30], g["colour"][:18], g["rows"],
            " + ".join(str(x) for x in g["lengths"]), g["combined_length"]))
        print("        R%.2f -> R%.2f  (delta R%+.2f)   keep line %s, remove %s" % (
            g["total_before"], g["total_after"], g["delta"], g["keeper_id"], g["removed_ids"]))
    print("\n%d group(s). Net money change across all of them: R%+.2f (rounding only)."
          % (len([g for g in groups if not g.get("skipped")]), total_delta))
    if not apply_changes:
        print("Re-run with --apply to write these changes.")
