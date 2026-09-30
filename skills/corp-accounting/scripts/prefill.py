#!/usr/bin/env python3
"""prefill.py — deterministic candidate extraction for Revenu Québec / CRA forms.

Produces a *draft* extracted-document JSON per anonymized PDF by pairing known
labels with their value (same line, else the next few lines), keeping page and a
verbatim snippet for provenance. The model reviews and corrects the draft;
nothing here is authoritative.

Usage:
    python3 prefill.py --vault PATH            # writes drafts to 20_Extracted/
    python3 prefill.py FILE --json             # one file, print draft
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402

FR_MONTHS = {
    "janvier": 1, "février": 2, "fevrier": 2, "mars": 3, "avril": 4, "mai": 5,
    "juin": 6, "juillet": 7, "août": 8, "aout": 8, "septembre": 9,
    "octobre": 10, "novembre": 11, "décembre": 12, "decembre": 12,
}
_MONTHS_ALT = "|".join(FR_MONTHS)
DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}\s+(?:" + _MONTHS_ALT + r")\s+\d{4}", re.I)
AMOUNT_RE = re.compile(r"(\d[\d\s ]*[,.]\d{2})\s*\$|(\d[\d\s ]*[,.]\d{2})(?!\d)")

FIELD_KIND = {"issued_date": "date", "due_date": "date", "amount": "amount", "period": "period"}

LABELS = [
    ("issued_date", re.compile(r"date de l[' ]avis|date d[' ]envoi", re.I)),
    ("due_date", re.compile(r"au plus tard|^\s*[ée]ch[ée]ance\b|\bapr[èe]s le\b|\bavant le\b", re.I)),
    ("amount", re.compile(
        r"somme due|montant d[ûu]|total de la cotisation|solde total|solde à payer"
        r"|un solde de|remboursement de", re.I)),
    ("period", re.compile(
        r"p[ée]riode couverte|p[ée]riode de d[ée]claration|^\s*ann[ée]e\s*:?\s*$"
        r"|date de cl[ôo]ture de l[' ]exercice", re.I)),
]

# order matters: explicit document headers before incidental mentions
TYPE_RULES = [
    ("RQ-NON-PRODUCTION", re.compile(r"avis de non-production", re.I)),
    ("RQ-COLLECTION", re.compile(r"avis de recouvrement", re.I)),
    ("RQ-REFUND-HOLD", re.compile(r"paiement ou crédit excédentaire|déclarations non produites", re.I)),
    ("RQ-TPS-TVH-ASSESSMENT", re.compile(r"avis de cotisation concernant la taxe sur les produits", re.I)),
    ("RQ-TPS-TVH-ASSESSMENT", re.compile(r"taxe perçue|taxe sur les intrants|\bRTI\b", re.I)),
    ("RQ-DAS-ASSESSMENT", re.compile(r"masse salariale|retenues à la source|\bRRQ\b|\bRQAP\b|dossier\s*:?\s*RS", re.I)),
    ("RQ-CO17-ASSESSMENT", re.compile(r"dossier\s*:?\s*IC|loi sur les imp[ôo]ts", re.I)),
]


def _norm_date(value: str):
    m = re.search(r"\d{4}-\d{2}-\d{2}", value)
    if m:
        return m.group(0)
    m = re.search(r"(\d{1,2})\s+(" + _MONTHS_ALT + r")\s+(\d{4})", value, re.I)
    if m:
        return f"{m.group(3)}-{FR_MONTHS[m.group(2).lower()]:02d}-{int(m.group(1)):02d}"
    return None


def _norm_amount(value: str):
    m = AMOUNT_RE.search(value)
    if not m:
        return None
    raw = (m.group(1) or m.group(2)).replace(" ", "").replace(",", ".")
    try:
        return f"{float(raw):.2f}"
    except ValueError:
        return None


def _extract_value(text: str, kind: str):
    if kind == "date":
        return _norm_date(text)
    if kind == "amount":
        return _norm_amount(text)
    t = text.strip(" :")
    if kind == "period" and re.search(r"\d", t):
        return t[:40]
    return None


def _pages(path: str):
    import pymupdf
    out = []
    with pymupdf.open(path) as doc:
        for page in doc:
            text = page.get_text("text")
            if len(page.get_text("words")) < 5:
                try:
                    from anonymize_core import ocr_words  # optional, if on path
                    text = " ".join(w[4] for w in ocr_words(page, "eng", 200))
                except Exception:
                    pass
            out.append(text)
    return out


def _classify(text: str) -> str:
    for name, rx in TYPE_RULES:
        if rx.search(text):
            return name
    return "RQ-CORRESPONDENCE"


def prefill(path: str) -> dict:
    pages = _pages(path)
    full = "\n".join(pages)
    doc = {
        "doc_id": V.sha256_file(path),
        "source_file": None,
        "jurisdiction": "RevenuQC" if re.search(
            r"revenu ?quebec|revenu québec|\bquébec\b|TPS/TVH|Loi sur les imp[ôo]ts", full, re.I) else "Unknown",
        "doc_type": _classify(full),
        "period": None,
        "issued_date": None,
        "dates": [],
        "amounts": [],
        "references": [],
        "confidence": "WARN",
        "notes": "deterministic prefill — review before trusting",
    }
    kind_for_dates = "response" if doc["doc_type"] in ("RQ-NON-PRODUCTION", "RQ-REFUND-HOLD") else "payment"

    for pno, text in enumerate(pages, 1):
        lines = [re.sub(r"\s+", " ", ln).strip() for ln in text.splitlines()]
        lines = [ln for ln in lines if ln]
        for i, line in enumerate(lines):
            for field, rx in LABELS:
                m = rx.search(line)
                if not m:
                    continue
                kind = FIELD_KIND[field]
                val = _extract_value(line[m.end():], kind)
                snippet = line
                if not val:
                    for j in range(i + 1, min(i + 4, len(lines))):
                        cand = lines[j]
                        if any(r.search(cand) for _, r in LABELS):
                            break
                        val = _extract_value(cand, kind)
                        if val:
                            snippet = f"{line} {cand}"
                            break
                if not val:
                    continue
                snippet = snippet[:180]
                if field == "issued_date" and not doc["issued_date"]:
                    doc["issued_date"] = val
                elif field == "due_date":
                    doc["dates"].append({"label": "payment/filing due", "date": val,
                                         "kind": kind_for_dates, "page": pno, "snippet": snippet})
                elif field == "amount":
                    doc["amounts"].append({"label": line[:60], "value": val,
                                           "page": pno, "snippet": snippet})
                elif field == "period" and not doc["period"]:
                    doc["period"] = val
                break

    # de-dup
    doc["dates"] = list({(d["date"], d["kind"]): d for d in doc["dates"] if d.get("date")}.values())
    doc["amounts"] = list({(a["label"], a["value"], a["page"]): a for a in doc["amounts"]}.values())
    return doc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--dir", help="scan this folder instead of 30_Anonymized/_Inbox")
    args = ap.parse_args()

    if args.file:
        print(json.dumps(prefill(os.path.expanduser(args.file)), indent=2, ensure_ascii=False))
        return 0

    vault = os.path.expanduser(args.vault)
    scan_dir = os.path.expanduser(args.dir) if args.dir else os.path.join(vault, V.INBOX_REL)
    out_dir = os.path.join(vault, V.EXTRACTED_REL)
    os.makedirs(out_dir, exist_ok=True)
    written = []
    for name in sorted(os.listdir(scan_dir)):
        if name.startswith(".") or not name.lower().endswith(".pdf"):
            continue
        path = os.path.join(scan_dir, name)
        doc = prefill(path)
        doc["source_file"] = os.path.relpath(path, vault)
        V.save_json(os.path.join(out_dir, f"{doc['doc_id']}.json"), doc)
        written.append((name, doc["doc_type"], doc["issued_date"], doc["period"]))
    for row in written:
        print("  ".join(str(x) for x in row))
    print(f"wrote {len(written)} draft(s) to {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
