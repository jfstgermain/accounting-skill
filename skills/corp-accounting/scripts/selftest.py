#!/usr/bin/env python3
"""selftest.py — exercise the corp-accounting pipeline on a throwaway vault.

Builds a temporary fake vault, drops synthetic anonymized PDFs (no real
content is parsed), and runs scan -> ledger upsert -> review -> reminders ->
file_docs. Asserts the expected end state. Run before trusting the pipeline on
real documents:

    python3 selftest.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import ledger as L  # noqa: E402
import reminders as R  # noqa: E402
import scan_inbox as S  # noqa: E402
import file_docs as F  # noqa: E402
import vaultlib as V  # noqa: E402

GOOD = "2026-05-12_CRA_GST-REMITTANCE_2026-04.pdf"
MISNAMED = "gst-reminder-may.pdf"


def _make_vault() -> str:
    root = tempfile.mkdtemp(prefix="corp-accounting-selftest-")
    for rel in (V.INBOX_REL, V.UNSORTED_REL, V.EXTRACTED_REL, V.LEDGER_REL):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    inbox = os.path.join(root, V.INBOX_REL)
    for name in (GOOD, MISNAMED):
        with open(os.path.join(inbox, name), "wb") as fh:
            fh.write(b"%PDF-1.4\n% synthetic test file " + name.encode() + b"\n")
    return root


def _extracted_for(vault: str, entry: dict) -> str:
    doc = {
        "doc_id": entry["sha256"],
        "source_file": entry["path"],
        "jurisdiction": "CRA",
        "doc_type": "GST-REMITTANCE",
        "period": "2026-04",
        "issued_date": "2026-05-12",
        "dates": [{"label": "GST remittance due", "date": "2026-06-15",
                   "kind": "payment", "page": 1, "snippet": "Payment due June 15, 2026"}],
        "amounts": [{"label": "Net tax", "value": "0.00", "page": 1, "snippet": "Net tax 0.00"}],
        "references": [],
        "confidence": "OK",
        "notes": "synthetic",
    }
    out = os.path.join(vault, V.EXTRACTED_REL, f"{entry['sha256'][:12]}.json")
    V.save_json(out, doc)
    return out


def main() -> int:
    vault = _make_vault()
    try:
        manifest = S.scan(vault)
        assert len(manifest["new"]) == 2, manifest["new"]
        by_name = {e["name"]: e for e in manifest["new"]}
        assert by_name[GOOD]["naming_ok"] is True
        assert by_name[MISNAMED]["naming_ok"] is False

        # model step, simulated
        for entry in manifest["new"]:
            extracted = _extracted_for(vault, entry)
            if entry["name"] == GOOD:
                L.upsert(vault, extracted)

        # second scan: the good one is now processed, the misnamed is untouched
        manifest2 = S.scan(vault)
        assert len(manifest2["already_processed"]) == 1, manifest2["already_processed"]
        assert len(manifest2["new"]) == 1

        # file only the processed one
        moved = F.file_documents(vault, manifest2)
        assert len(moved["moved"]) == 1, moved
        assert moved["moved"][0]["to"] == os.path.join("30_Anonymized", "CRA", GOOD)
        assert os.path.exists(os.path.join(vault, V.ANON_REL, "CRA", GOOD))

        # misnamed, if processed, would land in _Unsorted
        mis = S.scan(vault)["new"][0]
        L.upsert(vault, _extracted_for(vault, mis))
        moved2 = F.file_documents(vault, S.scan(vault))
        assert moved2["moved"][0]["to"] == os.path.join("30_Anonymized", "_Unsorted", MISNAMED), moved2

        # review + reminders
        rev = L.review(vault, None)
        assert os.path.exists(rev["out"])
        rem = R.stage(vault)
        assert len(rem["proposals"]) == 2, rem["proposals"]

        print("SELFTEST PASSED")
        print(f"  vault: {vault}")
        print(f"  review: {rev}")
        return 0
    finally:
        shutil.rmtree(vault, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
