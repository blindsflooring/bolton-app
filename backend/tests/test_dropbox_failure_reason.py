# -*- coding: utf-8 -*-
"""Can a Dropbox failure still be reported as "unknown error"?

Run:  python backend/tests/test_dropbox_failure_reason.py

WHY THIS EXISTS. Saving Quote #291 reported:

    "Saved locally, but the Dropbox upload failed: unknown error"

There is no such string anywhere in the backend. It is shared.js's own
fallback for `result.failure_reason || 'unknown error'` -- so the real
message was EMPTY, and the question is how an upload can fail without a
reason.

Because upload_document() recorded `str(e)`, and the exceptions a flaky
upload actually raises all stringify to nothing:

    requests.exceptions.ConnectionError   str(e) == ''
    requests.exceptions.ReadTimeout       str(e) == ''
    ConnectionResetError                  str(e) == ''
    OSError                               str(e) == ''

Dropbox's OWN api errors were always fine -- ApiError.__str__ returns
repr(self), carrying the request id and the error union -- which is the
cruel part: every failure the code was written for reported well, and every
failure it actually met reported nothing. The exception TYPE was the
diagnosis all along ("could not reach Dropbox"), and it was thrown away at
the point it was caught.

This test is the guarantee that no exception can be swallowed into an empty
reason again. It does NOT test that uploads succeed -- that needs a real
credential and a real network, and is not this file's job.
"""

import os
import socket
import ssl
import sys

BE = r"C:\Users\burge\blinds-flooring-bolton\bolton\backend"
sys.path.insert(0, BE)
os.chdir(BE)

import dropbox_archive                                            # noqa: E402

failures = []


def check(label, condition, detail=""):
    print(("  ok   " if condition else "  FAIL ") + label + (("  " + detail) if detail else ""))
    if not condition:
        failures.append(label + ((" -- " + detail) if detail else ""))


# Every one of these is a real exception this call path can raise. The first
# group is the one that broke: they carry no message at all.
SILENT = [
    ("requests ConnectionError", __import__("requests").exceptions.ConnectionError()),
    ("requests Timeout", __import__("requests").exceptions.Timeout()),
    ("requests ReadTimeout", __import__("requests").exceptions.ReadTimeout()),
    ("socket timeout", socket.timeout()),
    ("connection reset", ConnectionResetError()),
    ("bare OSError", OSError()),
    ("bare Exception", Exception()),
    ("SSL error", ssl.SSLError()),
]

print()
print("=" * 72)
print("A. THE EXCEPTIONS THAT USED TO VANISH")
print("=" * 72)
print("  Each of these has str(e) == '' (or as good as), which is exactly how")
print("  a failed upload ended up with no reason at all.")
print()

for label, exc in SILENT:
    was = str(exc)
    now = dropbox_archive.describe_failure(exc, "uploading to Dropbox",
                                           "/Bolton/Hermanus/Flooring/Test - Quote - v1.pdf",
                                           size_bytes=48210, seconds=30.2)
    print("  %-24s str(e)=%-6r" % (label, was))
    check("    -> has a reason at all", bool(now.strip()))
    check("    -> names the exception type", type(exc).__name__ in now)
    check("    -> would not read as 'unknown error'", now.strip() != "")

print()
print("=" * 72)
print("B. THE REASON IS ACTUALLY USEFUL, NOT JUST NON-EMPTY")
print("=" * 72)
print("  A non-empty string that says nothing would pass section A and still")
print("  leave the next person exactly where this one started.")
print()

import requests                                                   # noqa: E402

reason = dropbox_archive.describe_failure(
    requests.exceptions.ReadTimeout(), "uploading to Dropbox",
    "/Bolton/Hermanus/Flooring/Huis Theron - J-0019 - Quote - v2.pdf",
    size_bytes=48210, seconds=30.2)
print("  %s" % reason)
print()
check("names what it was doing", "uploading to Dropbox" in reason)
check("names the exception type", "ReadTimeout" in reason)
check("names the path, so a per-file problem is visible", "J-0019" in reason)
check("names the size, so a too-big file is visible", "47.1 KB" in reason)
check("names how long it waited, so a timeout is obvious", "30.2s" in reason)
check("says an absent message is itself the finding",
      "network error" in reason and "normal" in reason)

print()
print("  A Dropbox API error was never the broken case -- it must keep")
print("  reporting at least as well as it did before.")
print()

import dropbox                                                    # noqa: E402

api_error = dropbox.exceptions.ApiError(
    "req-abc123", dropbox.files.UploadError.other, None, None)
api_reason = dropbox_archive.describe_failure(api_error, "uploading to Dropbox",
                                              "/Bolton/Hermanus/x.pdf")
print("  %s" % api_reason[:150])
check("the request id survives, for Dropbox support", "req-abc123" in api_reason)
check("and so does the error union", "UploadError" in api_reason or "path" in api_reason)
check("and it is not worse than plain str(e)", len(api_reason) >= len(str(api_error)))

print()
print("=" * 72)
print("C. THE REAL CALL PATH, NOT JUST THE HELPER")
print("=" * 72)
print("  describe_failure() being correct is worth nothing if upload_document()")
print("  does not actually use it. Driven through the real function with the")
print("  credential check forced past and the Dropbox client made to explode.")
print()


class Exploding:
    def files_upload(self, *a, **kw):
        raise requests.exceptions.ConnectionError()

    def files_delete_v2(self, *a, **kw):
        raise requests.exceptions.ConnectionError()


original = dropbox_archive._get_client
dropbox_archive._get_client = lambda: Exploding()
try:
    result = dropbox_archive.upload_document(b"x" * 1024, "/Bolton/Hermanus/Real Path.pdf")
    print("  upload_document -> %s" % result)
    check("the real upload path reports a failure", result["ok"] is False)
    check("and NOT as a missing credential", result.get("not_configured") is False)
    check("and its reason is not empty", bool((result.get("reason") or "").strip()))
    check("and names the exception type", "ConnectionError" in result.get("reason", ""))
    check("and carries the path", "Real Path.pdf" in result.get("reason", ""))
    check("and reports the real size", "1.0 KB" in result.get("reason", ""))

    deleted = dropbox_archive.delete_document("/Bolton/Hermanus/Real Path.pdf")
    check("delete_document reports the same way", deleted["ok"] is False
          and "ConnectionError" in deleted.get("reason", ""))
finally:
    dropbox_archive._get_client = original

# A genuinely absent credential must still read as the calm, expected state
# it is -- "pending", not an alarming failure. That distinction is what stops
# the monitor blaming a credential that is fine, which cost four days once.
dropbox_archive._get_client = lambda: None
try:
    result = dropbox_archive.upload_document(b"x", "/Bolton/Hermanus/x.pdf")
    check("no credential still reads as not_configured, not as an error",
          result.get("not_configured") is True)
    check("and says so in words", "not connected" in result.get("reason", "").lower())
finally:
    dropbox_archive._get_client = original

print()
print("=" * 72)
print("D. THE FRONTEND NO LONGER BLAMES DROPBOX FOR EVERYTHING")
print("=" * 72)
print("  saveDocumentArchive() used to read res.json() without checking res.ok,")
print("  so a backend error -- a PDF that would not render, an expired session --")
print("  had no `status` field, fell through to the else, and was reported as a")
print("  Dropbox upload failure. Dropbox may never have been contacted at all.")
print()

with open(os.path.join(os.path.dirname(BE), "frontend", "shared.js"), encoding="utf-8") as fh:
    shared = fh.read()

start = shared.index("async function saveDocumentArchive")
body = shared[start:start + 3000]
check("saveDocumentArchive checks res.ok before reading a status", "if (!res.ok)" in body)
check("and surfaces the server's own detail", "body.detail" in body)
check("and does not claim the document was saved when it was not",
      "was NOT saved" in body)
check("and no longer says 'unknown error'", "unknown error" not in body)
check("and points at the copy Bolton itself holds",
      "Document History" in body)

start = shared.index("async function triggerArchiveDocument")
body = shared[start:start + 2000]
check("Archive now surfaces the server's detail too", "body.detail" in body)

check("the history panel shows something for a reason-less old row",
      "No reason was recorded" in shared)

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
print("No exception on the Dropbox path can be recorded as an empty reason any")
print("more, and a failure that never reached Dropbox is no longer reported as")
print("a Dropbox failure.")
print("=" * 72)
