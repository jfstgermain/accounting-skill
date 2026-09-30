#!/usr/bin/env python3
"""ledger.py — merge extracted documents into the compliance ledger.

Subcommands:
    upsert --extracted FILE [--vault PATH] [--json]
        Validate one extracted-document JSON, compute its OK/WARN/REVIEW state,
        store it in 40_Ledger/ledger.json, rebuild the obligations list, and
        record the document in 40_Ledger/processed.json (idempotency).

    review [--vault PATH] [--out FILE] [--json]
        Render 40_Ledger/review_queue.md from ledger.json.

Extracted-document JSON contract (produced by the model, validated here):
    {
      "doc_id": "<sha256>",              # must match the file hash
      "source_file": "30_Anonymized/_Inbox/....pdf",
      "jurisdiction": "CRA|RevenuQC|Payroll|Accountant|Unknown",
      "doc_type": "T2|CO-17|GST|QST|DAS|...",
      "period": "2025",
      "issued_date": "2026-03-31",
      "dates":    [{"label","date","kind","page","snippet"}],
      "amounts":  [{"label","value","page","snippet"}],
      "references":[{"label","value","page","snippet"}],
      "confidence": "OK|WARN|REVIEW",
      "notes": "..."
    }

This tool NEVER asserts that anything was filed, paid, or remitted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402

REQUIRED_KEYS = ("doc_id", "jurisdiction", "doc_type", "dates")
KIND_ORDER = {"filing": 0, "payment": 1, "instalment": 2, "response": 3, "other": 9}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _empty_ledger() -> dict:
    return {"version": 1, "updated_at": None, "documents": {}, "obligations": []}


def validate(doc: dict) -> list[str]:
    problems = [f"missing required key: {k}" for k in REQUIRED_KEYS if k not in doc]
    if not isinstance(doc.get("dates", []), list):
        problems.append("'dates' must be a list")
    for i, d in enumerate(doc.get("dates") or []):
        if not isinstance(d, dict):
            problems.append(f"dates[{i}] is not an object")
            continue
        for k in ("date", "kind", "page", "snippet"):
            if k not in d:
                problems.append(f"dates[{i}] missing '{k}'")
    return problems


def rebuild_obligations(documents: dict) -> list[dict]:
    obligations = []
    for doc_id, doc in documents.items():
        for d in doc.get("dates") or []:
            obligations.append({
                "id": f"{doc_id}:{d.get('date')}:{d.get('kind')}",
                "date": d.get("date"),
                "kind": d.get("kind"),
                "label": d.get("label", ""),
                "jurisdiction": doc.get("jurisdiction"),
                "doc_type": doc.get("doc_type"),
                "source_file": doc.get("source_file"),
                "page": d.get("page"),
                "snippet": d.get("snippet"),
                "state": doc.get("review_state", "OK"),
            })
    obligations.sort(key=lambda o: (o.get("date") or "9999", KIND_ORDER.get(o.get("kind"), 9)))
    return obligations


def upsert(vault: str, extracted_path: str) -> dict:
    V.ensure_vault(vault)
    doc = V.load_json(os.path.expanduser(extracted_path))
    if doc is None:
        raise SystemExit(f"cannot read extracted JSON: {extracted_path}")

    problems = validate(doc)
    if problems:
        raise SystemExit("invalid extracted document:\n  - " + "\n  - ".join(problems))

    doc_id = str(doc["doc_id"])
    state, tickets = V.review_state_for(doc)
    doc = dict(doc)
    doc["review_state"] = state
    doc["tickets"] = tickets
    doc["updated_at"] = _now()

    ledger_path = os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE)
    ledger = V.load_json(ledger_path, None) or _empty_ledger()
    ledger.setdefault("documents", {})
    ledger.setdefault("obligations", [])
    ledger["documents"][doc_id] = doc
    ledger["obligations"] = rebuild_obligations(ledger["documents"])
    ledger["updated_at"] = _now()
    V.save_json(ledger_path, ledger)

    processed_path = os.path.join(vault, V.LEDGER_REL, V.PROCESSED_FILE)
    processed = V.load_json(processed_path, {}) or {}
    source = doc.get("source_file")
    processed[doc_id] = {
        "name": os.path.basename(source) if source else doc_id,
        "original_path": source,
        "file_path": doc.get("file_path") or source,
        "processed_at": _now(),
        "doc_id": doc_id,
    }
    V.save_json(processed_path, processed)

    return {"doc_id": doc_id, "review_state": state, "tickets": tickets,
            "obligations": [o for o in ledger["obligations"] if o["id"].startswith(doc_id + ":")]}


def _render_review(ledger: dict) -> str:
    docs = ledger.get("documents", {})
    obligations = ledger.get("obligations", [])
    lines = ["# Corp Accounting — Review Queue", "", f"_Generated: {_now()}_", ""]

    counts = {"OK": 0, "WARN": 0, "REVIEW": 0}
    for d in docs.values():
        counts[d.get("review_state", "OK")] = counts.get(d.get("review_state", "OK"), 0) + 1
    lines.append(f"Documents: {len(docs)} | OK {counts['OK']} · WARN {counts['WARN']} · REVIEW {counts['REVIEW']}")
    lines.append("")

    if obligations:
        lines.append("## Obligations")
        lines.append("")
        lines.append("| Date | Kind | State | Jurisdiction | Type | Label |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for o in obligations:
            lines.append(
                f"| {o.get('date')} | {o.get('kind')} | {o.get('state')} | "
                f"{o.get('jurisdiction')} | {o.get('doc_type')} | {o.get('label', '')} |")
        lines.append("")

    flagged = {k: v for k, v in docs.items() if v.get("review_state") in ("REVIEW", "WARN")}
    if flagged:
        lines.append("## Tickets")
        lines.append("")
        for doc_id, doc in flagged.items():
            lines.append(f"### {doc.get('review_state')} — {doc.get('doc_type')} ({doc.get('period', '?')})")
            lines.append(f"- source: `{doc.get('source_file')}`")
            for t in doc.get("tickets", []):
                lines.append(f"- {t}")
            lines.append("")
    else:
        lines.append("_No WARN or REVIEW items._")
        lines.append("")

    lines.append("> Nothing here is ever filed, paid, or remitted automatically. A human acts on every REVIEW ticket.")
    lines.append("")
    return "\n".join(lines)


def review(vault: str, out: str | None) -> dict:
    ledger = V.load_json(os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE), None) or _empty_ledger()
    text = _render_review(ledger)
    out = out or os.path.join(vault, V.LEDGER_REL, V.REVIEW_FILE)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(text)
    return {"out": out, "documents": len(ledger.get("documents", {})),
            "obligations": len(ledger.get("obligations", []))}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_up = sub.add_parser("upsert", help="merge one extracted document")
    p_up.add_argument("--extracted", required=True)
    p_up.add_argument("--json", action="store_true")

    p_rv = sub.add_parser("review", help="render review_queue.md")
    p_rv.add_argument("--out")
    p_rv.add_argument("--json", action="store_true")

    args = ap.parse_args()
    vault = os.path.expanduser(args.vault)

    if args.cmd == "upsert":
        result = upsert(vault, args.extracted)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"{result['review_state']}: {result['doc_id'][:12]}  "
                  f"({len(result['obligations'])} obligation(s))")
            for t in result["tickets"]:
                print(f"  - {t}")
        return 0

    if args.cmd == "review":
        result = review(vault, os.path.expanduser(args.out) if args.out else None)
        if args.json:
            print(json.dumps(result, indent=2, ensure_ascii=False))
        else:
            print(f"wrote {result['out']} ({result['documents']} docs, {result['obligations']} obligations)")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
