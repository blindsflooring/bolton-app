# -*- coding: utf-8 -*-
"""Restore a Bolton pg_dump into a throwaway Postgres, using psycopg2 only.

WHY THIS EXISTS. RESTORE_BACKUP.md says `psql "$DATABASE_URL" < backup.sql`,
and that is still the right instruction when psql is available. It was not
available on the machine this test ran on - no psql, no pg_restore, no
docker - and Supabase's SQL editor cannot ingest a 12.9MB dump by paste.
psycopg2 was already installed, so the restore is driven through that.

It also handles something a naive executor gets wrong: pg_dump 18 emits
psql META-COMMANDS (\\restrict, \\unrestrict) that are not SQL and that
the server will reject. psql swallows them; anything else must skip them.

The connection string is read from a file and never printed, never logged,
and never echoed into an error message.
"""
import gzip
import io
import os
import re
import sys

import psycopg2

HERE = os.path.dirname(os.path.abspath(__file__))
URL_FILE = os.path.join(HERE, "target-url.txt")


def read_target_url():
    if not os.path.exists(URL_FILE):
        sys.exit("No target-url.txt found in %s - see the instructions." % HERE)
    url = open(URL_FILE, encoding="utf-8").read().strip()
    if not url.startswith("postgres"):
        sys.exit("target-url.txt does not look like a postgres:// URL.")
    return url


def load_sql(path):
    raw = open(path, "rb").read()
    if path.endswith(".gz"):
        raw = gzip.decompress(raw)
    return raw.decode("utf-8", errors="replace")


COPY_RE = re.compile(r'^COPY .+ FROM stdin;$')


def statements(sql_text):
    """Yield ('sql', text) and ('copy', header, body) in file order.

    Deliberately simple, and it says so: it tracks dollar-quoted blocks
    ($$ ... $$) so a function body's semicolons do not split a statement,
    skips psql meta-commands, and treats COPY ... FROM stdin as a block
    terminated by a lone backslash-dot. That is the whole grammar a
    Bolton pg_dump actually uses.
    """
    buf, in_dollar = [], False
    lines = sql_text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]

        # Blank and comment lines, while nothing is buffered, are
        # discarded rather than accumulated. Keeping them broke two
        # things at once in testing: the comment block ahead of a
        # statement made the whole statement look like a comment and it
        # was dropped, and a non-empty buffer stopped COPY being
        # recognised at all, so a data block was executed as SQL.
        if not buf and not in_dollar and (not line.strip() or line.lstrip().startswith("--")):
            i += 1
            continue

        if not buf and COPY_RE.match(line.strip()):
            header = line.strip()
            body, i = [], i + 1
            while i < len(lines) and lines[i] != chr(92) + ".":
                body.append(lines[i])
                i += 1
            yield ("copy", header, "\n".join(body) + ("\n" if body else ""))
            i += 1
            continue

        # psql meta-commands: not SQL, the server rejects them.
        if not buf and not in_dollar and line.startswith(chr(92)):
            i += 1
            continue

        if line.count("$$") % 2 == 1:
            in_dollar = not in_dollar

        buf.append(line)
        if not in_dollar and line.rstrip().endswith(";"):
            text = "\n".join(buf).strip()
            buf = []
            if text and not text.startswith("--"):
                yield ("sql", text, None)
        i += 1

    if buf:
        text = "\n".join(buf).strip()
        if text and not text.startswith("--"):
            yield ("sql", text, None)


def main():
    if len(sys.argv) < 2:
        sys.exit("usage: restore_driver.py <backup.sql|backup.sql.gz> [--go]")
    path = sys.argv[1]
    go = "--go" in sys.argv

    sql_text = load_sql(path)
    items = list(statements(sql_text))
    kinds = {}
    for k, *_ in items:
        kinds[k] = kinds.get(k, 0) + 1
    print("parsed %d statements (%s)" % (len(items), kinds))
    if not go:
        print("dry run - pass --go to actually execute against the target")
        return

    url = read_target_url()
    conn = psycopg2.connect(url)
    conn.autocommit = True
    cur = conn.cursor()

    ok = failed = copied_rows = 0
    errors = []
    for kind, a, b in items:
        try:
            if kind == "copy":
                cur.copy_expert(a, io.StringIO(b))
                copied_rows += b.count("\n") if b else 0
            else:
                cur.execute(a)
            ok += 1
        except Exception as e:
            failed += 1
            # first line only, and never the statement's data
            errors.append((a.split("\n")[0][:110], str(e).split("\n")[0][:130]))

    print()
    print("executed OK : %d" % ok)
    print("failed      : %d" % failed)
    print("rows COPYed : %d" % copied_rows)
    if errors:
        print()
        print("first 25 failures:")
        seen = set()
        for stmt, err in errors[:25]:
            key = err[:60]
            mark = "" if key in seen else ""
            seen.add(key)
            print("  %-70s -> %s" % (stmt, err))
    conn.close()


if __name__ == "__main__":
    main()
