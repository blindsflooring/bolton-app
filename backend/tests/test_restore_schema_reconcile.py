# -*- coding: utf-8 -*-
"""Does Bolton's startup reconcile an OLDER backup's schema?

Run:  python backend/tests/test_restore_schema_reconcile.py

WHY THIS EXISTS. The restore path was proven end to end in September 2026
(PR #38): the nightly Dropbox backup restores, byte-for-byte, into an empty
database. But a restore is only half of a recovery. The other half is what
happens when the CURRENT application boots against that restored database,
because the backup is always older than the code -- and the older it is, the
more the models have moved on. The 27 August dump was missing 12 whole tables
and 78 columns against the models three weeks later.

Nobody had ever pointed the app at one and watched. That was the last untested
piece of the restore path, and this test is it.

Three things are supposed to close that gap at boot, in this order:

    SQLModel.metadata.create_all()   adds tables that do not exist
    _reconcile_model_columns()       adds columns the models declare and the
                                     database lacks, derived from SQLModel's
                                     own metadata so it cannot be forgotten
    _check_schema_matches_models()   records anything still missing

This builds a genuinely old database -- the real models.py from 27 August 2026,
pulled out of git history, not a hand-written approximation -- seeds it with
real rows, and then boots the CURRENT backend against it and checks that:

  1. the boot completes at all;
  2. the missing tables are created and the missing columns added;
  3. _check_schema_matches_models() afterwards reports nothing missing;
  4. THE OLD ROWS ARE STILL THERE, unchanged, and readable through the
     current ORM -- which is the actual point. A reconciliation that
     dropped and recreated would pass 1-3 and lose the business.

KNOWN LIMIT, stated rather than glossed: this runs against SQLite, because
this machine has no Postgres, no psql and no Docker. The three functions above
are dialect-agnostic apart from column.type.compile(), which is exactly where
a Postgres-only difference could still hide. So this proves the RECONCILIATION
LOGIC is correct and non-destructive; it does not prove the Supabase restore
end to end. That still wants one run against a throwaway Postgres project.

Runs against a throwaway SQLite file - never production, never the dev database.
"""

import contextlib
import io
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile

BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
REPO = os.path.dirname(BE)

# HEAD on 27 August 2026 -- the same vintage as the oldest backup sitting in
# the old Dropbox folder, and the one the 12-tables/78-columns gap was measured
# against. Pinned deliberately: the point of this test is a KNOWN-old schema.
OLD_COMMIT = "d8995fd"

# Rows seeded into the old database, before the current code ever sees it.
# Checked again, field by field, after the boot.
SEED_CLIENT = {"name": "Huis Restore Test", "phone": "082 555 0101",
               "preferred_branch": "hermanus", "marketing_source": "Referral"}
SEED_QUOTE = {"client_name": "Huis Restore Test", "sales_owner": "ryno",
              "branch": "hermanus", "status": "accepted",
              "workflow_status": "accepted", "job_number": "J-9001",
              "deposit_pct": 0.70}
SEED_LINE = {"category": "flooring", "product_id": 1,
             "product_name": "Aspen Herringbone Range 2mm"}


# --------------------------------------------------------------------------
# Child mode. Builds the old-schema database in its OWN process, because
# SQLModel.metadata is a single global registry -- importing the 27 August
# models and the current models into one interpreter collides on every table
# name they share, and the second one silently loses.
# --------------------------------------------------------------------------
def build_old_database(db_path, old_models_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("old_models_27aug", old_models_path)
    old = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(old)

    from sqlalchemy import inspect
    from sqlmodel import SQLModel, Session, create_engine

    engine = create_engine("sqlite:///" + db_path.replace("\\", "/"))
    SQLModel.metadata.create_all(engine)

    with Session(engine) as s:
        s.add(old.Client(tenant_id="1", **SEED_CLIENT))
        q = old.Quote(tenant_id="1", **SEED_QUOTE)
        s.add(q)
        s.flush()
        s.add(old.QuoteLineItem(tenant_id="1", quote_id=q.id, **SEED_LINE))
        s.commit()
        quote_id = q.id

    insp = inspect(engine)
    schema = {t: sorted(c["name"] for c in insp.get_columns(t))
              for t in insp.get_table_names()}
    print("RESULT_JSON:" + json.dumps({"schema": schema, "quote_id": quote_id}))


if len(sys.argv) > 1 and sys.argv[1] == "--build-old":
    build_old_database(sys.argv[2], sys.argv[3])
    sys.exit(0)


# --------------------------------------------------------------------------
# Parent.
# --------------------------------------------------------------------------
sys.path.insert(0, BE)
os.chdir(BE)

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(label + ((" -- " + detail) if detail else ""))


tmpdir = tempfile.gettempdir()
db_path = os.path.join(tmpdir, "bolton_restore_reconcile_test.db")
old_models_path = os.path.join(tmpdir, "bolton_models_%s.py" % OLD_COMMIT)
for stale in (db_path, old_models_path):
    if os.path.exists(stale):
        os.remove(stale)

print("=" * 72)
print("A. BUILD AN OLD DATABASE -- the real models.py from %s (27 Aug 2026)" % OLD_COMMIT)
print("=" * 72)

old_source = subprocess.run(["git", "show", "%s:backend/models.py" % OLD_COMMIT],
                            cwd=REPO, capture_output=True)
if old_source.returncode != 0:
    print("  FAIL could not read models.py at %s from git" % OLD_COMMIT)
    print(old_source.stderr.decode("utf-8", "replace"))
    sys.exit(1)
with open(old_models_path, "wb") as fh:
    fh.write(old_source.stdout)
print("  old models.py  : %d lines" % old_source.stdout.count(b"\n"))

child = subprocess.run([sys.executable, os.path.abspath(__file__),
                        "--build-old", db_path, old_models_path],
                       capture_output=True)
payload = None
for line in child.stdout.decode("utf-8", "replace").splitlines():
    if line.startswith("RESULT_JSON:"):
        payload = json.loads(line[len("RESULT_JSON:"):])
if payload is None:
    print("  FAIL could not build the old database")
    print(child.stdout.decode("utf-8", "replace")[-3000:])
    print(child.stderr.decode("utf-8", "replace")[-3000:])
    sys.exit(1)

old_schema = payload["schema"]
seeded_quote_id = payload["quote_id"]
print("  old database   : %d tables, %d columns, seeded with a client, "
      "a quote (%s) and a line"
      % (len(old_schema), sum(len(c) for c in old_schema.values()), SEED_QUOTE["job_number"]))

print()
print("=" * 72)
print("B. THE GAP -- what the current models declare that this database lacks")
print("=" * 72)

os.environ["DATABASE_URL"] = "sqlite:///" + db_path.replace("\\", "/")

from sqlmodel import SQLModel, Session, select  # noqa: E402
import models  # noqa: E402,F401  (registers the CURRENT metadata)

with open(os.path.join(BE, "main.py"), encoding="utf-8") as fh:
    main_source = fh.read()

current_tables = dict(SQLModel.metadata.tables)
missing_tables = sorted(t for t in current_tables if t not in old_schema)
missing_columns = []
for name, table in current_tables.items():
    if name not in old_schema:
        continue
    for column in table.columns:
        if column.name not in old_schema[name]:
            missing_columns.append("%s.%s" % (name, column.name))
missing_columns.sort()

print("  missing tables : %d   %s" % (len(missing_tables), ", ".join(missing_tables)))
print("  missing columns: %d" % len(missing_columns))
for item in missing_columns[:8]:
    print("                   %s" % item)
if len(missing_columns) > 8:
    print("                   ... and %d more" % (len(missing_columns) - 8))

check("the gap is real, not an empty test",
      len(missing_tables) > 0 and len(missing_columns) > 0,
      "%d tables, %d columns" % (len(missing_tables), len(missing_columns)))

print()
print("=" * 72)
print("C. FORCE THE SAFETY NET TO FIRE")
print("=" * 72)
print("""  Bolton has TWO mechanisms that add missing columns, and the first one
  hides the second. _ensure_new_columns() is a hand-written list of ~175
  (table, column) tuples; _reconcile_model_columns() derives the same thing
  from SQLModel's own metadata and exists precisely because the hand list can
  be forgotten -- that is how quotelineitem.nosing_product_id took the Order
  Index and the KPI dashboard down on 14 Sept 2026.

  On a straight run of this test the hand list turns out to cover the whole
  27-August gap, so the reconciler adds nothing and is never exercised. A
  green test would then be saying nothing at all about the mechanism that
  actually matters when somebody forgets. So: drop a column the hand list
  does NOT cover, and require the reconciler to be the thing that restores
  it.""")

hand_list = set(re.findall(
    r'\(\s*"([a-z_]+)"\s*,\s*"([a-z_0-9]+)"\s*,',
    main_source[main_source.index("def _ensure_new_columns"):
                main_source.index("def _column_default_sql")]))
print()
print("  hand list covers: %d (table, column) pairs" % len(hand_list))

seeded_fields = ({("client", f) for f in SEED_CLIENT}
                 | {("quote", f) for f in SEED_QUOTE}
                 | {("quotelineitem", f) for f in SEED_LINE})
candidates = []
for name, table in current_tables.items():
    if name not in old_schema:
        continue
    for column in table.columns:
        key = (name, column.name)
        if (column.name in old_schema[name] and key not in hand_list
                and key not in seeded_fields and not column.primary_key
                and not column.index and not column.name.endswith("_id")
                and column.name != "id"):
            candidates.append(key)

dropped = None
conn = sqlite3.connect(db_path)
for table_name, column_name in candidates:
    try:
        conn.execute("ALTER TABLE %s DROP COLUMN %s" % (table_name, column_name))
        conn.commit()
        dropped = (table_name, column_name)
        break
    except sqlite3.OperationalError:
        continue          # indexed / constrained / otherwise undroppable
conn.close()

check("a hand-list-uncovered column could be dropped to test the reconciler",
      dropped is not None,
      "" if dropped is None else "dropped %s.%s" % dropped)

print()
print("=" * 72)
print("D. BOOT THE CURRENT BACKEND AGAINST IT")
print("=" * 72)

import main  # noqa: E402

booted = True
boot_error = ""
startup_log = io.StringIO()
try:
    with contextlib.redirect_stdout(startup_log):
        main.on_startup()
except Exception as exc:                     # noqa: BLE001 -- the whole question
    booted = False
    boot_error = "%s: %s" % (type(exc).__name__, exc)
startup_output = startup_log.getvalue()
print(startup_output)

print()
check("the backend boots against an old restored database", booted, boot_error)
if not booted:
    print()
    print("FAILURES: " + boot_error)
    sys.exit(1)

print()
print("=" * 72)
print("E. DID IT RECONCILE?")
print("=" * 72)

from sqlalchemy import inspect  # noqa: E402

insp = inspect(main.engine)
after = {t: sorted(c["name"] for c in insp.get_columns(t)) for t in insp.get_table_names()}

created = sorted(t for t in missing_tables if t in after)
not_created = sorted(t for t in missing_tables if t not in after)
added = [c for c in missing_columns
         if c.split(".", 1)[0] in after and c.split(".", 1)[1] in after[c.split(".", 1)[0]]]
not_added = [c for c in missing_columns if c not in added]

print("  tables  created: %d of %d" % (len(created), len(missing_tables)))
print("  columns added  : %d of %d" % (len(added), len(missing_columns)))
if not_created:
    print("  NOT created    : %s" % ", ".join(not_created))
if not_added:
    print("  NOT added      : %s" % ", ".join(not_added))

check("every missing table was created", not not_created)
check("every missing column was added", not not_added)

# WHICH mechanism did the work. The hand list logs "Migration: added ...";
# the metadata-derived reconciler logs "Schema: added missing column ...".
by_hand_list = re.findall(r"^Migration: added (\S+) to (\S+),", startup_output, re.M)
by_reconciler = re.findall(r"^Schema: added missing column (\S+)", startup_output, re.M)
print()
print("  added by the hand list      : %d" % len(by_hand_list))
print("  added by the reconciler     : %d   %s"
      % (len(by_reconciler), ", ".join(by_reconciler) or "-"))

if dropped is not None:
    target = "%s.%s" % dropped
    live = after.get(dropped[0], [])
    check("the dropped column is back (%s)" % target, dropped[1] in live)
    check("and the RECONCILER is what restored it, not the hand list",
          target in by_reconciler,
          "" if target in by_reconciler else "reconciler logged %r" % (by_reconciler,))

check("no ALTER was silently swallowed",
      "Migration: FAILED" not in startup_output
      and "Schema: FAILED" not in startup_output,
      "see the startup log above")

print()
print("  and the startup check's own verdict:")
print("    checked_at    : %s" % main.SCHEMA_CHECK["checked_at"])
print("    tables_checked: %s" % main.SCHEMA_CHECK["tables_checked"])
print("    missing tables: %s" % (main.SCHEMA_CHECK["missing_tables"] or "none"))
print("    missing cols  : %s" % (main.SCHEMA_CHECK["missing"] or "none"))

check("_check_schema_matches_models() reports no missing tables",
      not main.SCHEMA_CHECK["missing_tables"])
check("_check_schema_matches_models() reports no missing columns",
      not main.SCHEMA_CHECK["missing"])
check("the startup check actually ran", bool(main.SCHEMA_CHECK["checked_at"]))

print()
print("=" * 72)
print("F. DID THE BUSINESS SURVIVE?  (the part that actually matters)")
print("=" * 72)

with Session(main.engine) as s:
    quote = s.exec(select(models.Quote).where(models.Quote.id == seeded_quote_id)).first()
    client = s.exec(select(models.Client).where(
        models.Client.name == SEED_CLIENT["name"])).first()
    lines = s.exec(select(models.QuoteLineItem).where(
        models.QuoteLineItem.quote_id == seeded_quote_id)).all()

    check("the seeded quote is still there", quote is not None)
    check("the seeded client is still there", client is not None)
    check("the seeded line is still there", len(lines) == 1,
          "found %d" % len(lines))

    if quote is not None:
        for field, expected in SEED_QUOTE.items():
            actual = getattr(quote, field)
            check("quote.%s survived" % field, actual == expected,
                  "" if actual == expected else "expected %r, got %r" % (expected, actual))
    if client is not None:
        for field, expected in SEED_CLIENT.items():
            actual = getattr(client, field)
            check("client.%s survived" % field, actual == expected,
                  "" if actual == expected else "expected %r, got %r" % (expected, actual))
    if len(lines) == 1:
        for field, expected in SEED_LINE.items():
            actual = getattr(lines[0], field)
            check("line.%s survived" % field, actual == expected,
                  "" if actual == expected else "expected %r, got %r" % (expected, actual))

    if quote is not None:
        # A column that did NOT exist on 27 August, read through the current
        # ORM on a row written before it existed. This is the read that used
        # to 500 in production when nosing_product_id went missing.
        newly_added = [c for c in missing_columns if c.startswith("quote.")]
        if newly_added:
            field = newly_added[0].split(".", 1)[1]
            try:
                value = getattr(quote, field)
                ok = True
            except Exception as exc:          # noqa: BLE001
                value, ok = str(exc), False
            check("a brand-new column reads on an old row (quote.%s)" % field, ok,
                  "= %r" % (value,))

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
print("An old backup restored into an empty database and booted against the")
print("current code reconciles itself: %d table(s) and %d column(s) added at"
      % (len(created), len(added)))
print("startup, nothing reported missing afterwards, and every seeded row came")
print("through unchanged.")
print()
print("Both mechanisms were exercised, not just the first one: the hand list")
print("added %d column(s), and _reconcile_model_columns() added %s -- the one"
      % (len(by_hand_list), ", ".join(by_reconciler) or "nothing"))
print("the hand list did not know about.")
print()
print("LIMIT: SQLite, not Postgres. See this file's docstring.")
print("=" * 72)
