#!/usr/bin/env python3
"""bank_statements.py — finalize bank-statement PDFs into the ledger.

Reads the statement summary (opening balance, deposits, withdrawals, closing balance and
period) from each bank-statement PDF in the inbox and produces a `BANK-STATEMENT` record
with provenance, then upserts it. Works for both the RBC business wording
("Solde d'ouverture", "Total des chèques et des débits") and the RBC personal wording
("Votre solde d'ouverture", "Total des retraits"). The summary figures are the authoritative
per-period balances, so they also drive the statement validation in `reconcile.py`.

Usage:
    bank_statements.py --vault PATH [--dir DIR] [--json]
Then: file_docs.py --vault PATH ; ledger.py review --vault PATH
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402
import ledger as L  # noqa: E402

MFR = {"janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5, "juin": 6,
       "juillet": 7, "août": 8, "aout": 8, "septembre": 9, "octobre": 10, "novembre": 11,
       "décembre": 12, "decembre": 12}

# substring -> canonical label
LABELS = [
    ("solde d'ouverture", "Solde d'ouverture"),
    ("total des dépôts", "Total des dépôts"),
    ("total des depots", "Total des dépôts"),
    ("total des retraits", "Total des retraits"),
    ("total des chèques", "Total des chèques et débits"),
    ("total des cheques", "Total des chèques et débits"),
    ("solde de clôture", "Solde de clôture"),
    ("solde de cloture", "Solde de clôture"),
]


def _frdate(s):
    m = re.search(r"(\d{1,2})(?:er|e)?\s+([A-Za-zéûà]+)\s+(\d{4})", s or "", re.I)
    if not m:
        return None
    mo = MFR.get(m.group(2).lower())
    return f"{m.group(3)}-{mo:02d}-{int(m.group(1)):02d}" if mo else None


def _num(s):
    m = re.search(r"([-+=]?\s*[\d\s]+,\d{2})", s or "")
    if not m:
        return None
    return float(m.group(1).replace(" ", "").replace("+", "").replace("=", "").replace(",", "."))


def _next(lines, i):
    for j in range(i + 1, min(i + 3, len(lines))):
        if lines[j].strip():
            return lines[j]
    return ""


def parse_statement(path):
    import pymupdf
    doc = pymupdf.open(path)
    text = doc[0].get_text()
    doc.close()
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    out = {"figures": {}, "period": None, "end": None}
    for i, line in enumerate(lines):
        low = line.lower()
        if out["period"] is None and low.startswith("du ") and " au " in low:
            a, b = low[3:].split(" au ", 1)
            if _frdate(a) and _frdate(b):
                out["period"] = (_frdate(a), _frdate(b))
        for key, label in LABELS:
            if key in low:
                out["figures"].setdefault(label, _num(_next(lines, i)))
                if label == "Solde de clôture" and out["end"] is None:
                    out["end"] = _frdate(line)
                break
    return out


def finalize(vault, scan_dir=None):
    inbox = os.path.abspath(scan_dir) if scan_dir else os.path.join(vault, V.INBOX_REL)
    done, skipped = [], []
    for root, _dirs, files in os.walk(inbox):
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith(".pdf"):
                continue
            path = os.path.join(root, name)
            info = parse_statement(path)
            if not info["period"] or not info["end"]:
                skipped.append(name)
                continue
            start, end = info["period"]
            digest = V.sha256_file(path)
            out = os.path.join(vault, V.EXTRACTED_REL, f"{digest}.json")
            doc = V.load_json(out, None) or {"doc_id": digest}
            doc.update(doc_type="BANK-STATEMENT", jurisdiction="Bank", issued_date=end,
                       period=end[:7], dates=[], confidence="OK", topic=None,
                       source_file=os.path.relpath(path, vault),
                       amounts=[{"label": k, "value": f"{v:.2f}", "page": 1,
                                 "snippet": f"{k} {v:,.2f}"}
                                for k, v in info["figures"].items() if v is not None],
                       notes=f"Relevé bancaire (période {start} → {end}).")
            V.save_json(out, doc)
            L.upsert(vault, out)
            done.append(name)
    return {"inbox": inbox, "finalized": done, "skipped": skipped}


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--dir", help="folder to scan (default: 30_Anonymized/_Inbox)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    vault = os.path.expanduser(args.vault)
    res = finalize(vault, os.path.expanduser(args.dir) if args.dir else None)
    if args.json:
        print(json.dumps(res, indent=2, ensure_ascii=False))
    else:
        print(f"inbox: {res['inbox']}")
        print(f"finalized {len(res['finalized'])} statement(s)")
        for s in res["skipped"]:
            print(f"  skipped (not a parsable statement): {s}")
    return 0 if res["finalized"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
