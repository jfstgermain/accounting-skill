#!/usr/bin/env python3
"""selftest.py — exercise the corp-accounting pipeline on a throwaway vault.

Covers three filing paths:
  * conforming filename -> kept, filed by name
  * non-conforming name but a known jurisdiction -> renamed to the convention
  * unknown jurisdiction -> _Unsorted (REVIEW)

Run before trusting the pipeline on real documents:

    python3 selftest.py
"""

from __future__ import annotations

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

GOOD = "2026-05-12_CRA_GST-REMITTANCE_2026-04.pdf"      # conforming
HASHY = "1cc8e5302c36b78e2ab9ce9320ad6a59_anon.pdf"     # hash-named, jurisdiction known
OPAQUE = "d38fbd239951c51ba088a2752f8f8252_anon.pdf"    # hash-named, jurisdiction unknown


def _make_vault() -> str:
    root = tempfile.mkdtemp(prefix="corp-accounting-selftest-")
    for rel in (V.INBOX_REL, V.UNSORTED_REL, V.EXTRACTED_REL, V.LEDGER_REL):
        os.makedirs(os.path.join(root, rel), exist_ok=True)
    inbox = os.path.join(root, V.INBOX_REL)
    for name in (GOOD, HASHY, OPAQUE):
        with open(os.path.join(inbox, name), "wb") as fh:
            fh.write(b"%PDF-1.4\n% synthetic test file " + name.encode() + b"\n")
    return root


def _extracted_for(vault: str, entry: dict, **overrides) -> str:
    doc = {
        "doc_id": entry["sha256"],
        "source_file": entry["path"],
        "jurisdiction": "CRA",
        "doc_type": "GST-REMITTANCE",
        "period": "2026-04",
        "issued_date": "2026-05-12",
        "dates": [{"label": "GST remittance due", "date": "2027-06-15",
                   "kind": "payment", "page": 1, "snippet": "Payment due June 15, 2027"}],
        "amounts": [{"label": "Net tax", "value": "0.00", "page": 1, "snippet": "Net tax 0.00"}],
        "references": [],
        "confidence": "OK",
        "notes": "synthetic",
    }
    doc.update(overrides)
    out = os.path.join(vault, V.EXTRACTED_REL, f"{entry['sha256'][:12]}.json")
    V.save_json(out, doc)
    return out


def main() -> int:
    vault = _make_vault()
    try:
        manifest = S.scan(vault)
        assert len(manifest["new"]) == 3, manifest["new"]
        by_name = {e["name"]: e for e in manifest["new"]}
        assert by_name[GOOD]["naming_ok"] is True
        assert by_name[HASHY]["naming_ok"] is False
        assert by_name[OPAQUE]["naming_ok"] is False

        # model step, simulated
        L.upsert(vault, _extracted_for(vault, by_name[GOOD]))
        L.upsert(vault, _extracted_for(
            vault, by_name[HASHY],
            jurisdiction="RevenuQC", doc_type="CO-17-ASSESSMENT",
            period="2025", issued_date="2025-10-22"))
        L.upsert(vault, _extracted_for(
            vault, by_name[OPAQUE],
            jurisdiction="Unknown", doc_type="Unknown",
            period=None, issued_date=None, confidence="REVIEW"))

        # file everything now that it is all processed
        result = F.file_documents(vault, S.scan(vault))
        dests = {m["from"].split("/")[-1]: m["to"] for m in result["moved"]}

        assert dests[GOOD] == os.path.join("30_Anonymized", "CRA", GOOD), dests[GOOD]
        assert dests[HASHY] == os.path.join("30_Anonymized", "RevenuQC",
                                            "2025-10-22_RQ_CO-17-ASSESSMENT_2025.pdf"), dests[HASHY]
        assert dests[OPAQUE] == os.path.join("30_Anonymized", "_Unsorted", OPAQUE), dests[OPAQUE]
        assert len(result["renamed"]) == 1, result["renamed"]

        # inbox is now empty; nothing to re-file
        assert S.scan(vault)["new"] == []

        rev = L.review(vault, None)
        assert os.path.exists(rev["out"])
        rem = R.stage(vault)
        assert len(rem["proposals"]) == 2, rem["proposals"]  # two known jurisdictions

        print("SELFTEST PASSED")
        print(f"  renamed: {result['renamed']}")
        print(f"  review:  {rev}")
        return 0
    finally:
        shutil.rmtree(vault, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
