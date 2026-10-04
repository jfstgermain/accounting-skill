#!/usr/bin/env python3
"""reconcile.py — three-way reconciliation: ledger <-> bank payments.

Matches the obligations and document amounts in the ledger against the government payments
in the derived payments store, and reports:

  A. government payments seen in the bank (by category / year)
  B. matches (bank payment <-> a document amount and/or a dated obligation)
  C. unexplained bank payments — to a government, matching nothing in the ledger
  D. obligations with no government payment near their due date (heuristic — verify)

What it cannot see: how the government *applied* a payment (which period / account). That
only lives on the statement of account (relevé). A payment applied elsewhere than expected
shows up as (C) plus a surplus in (A) — pull the statement of account for that period.

Usage:
    reconcile.py --vault PATH [--from YYYY-MM-DD] [--to YYYY-MM-DD]
        [--tol-days N] [--lookback-days N] [--out FILE] [--json]
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
import payments as P  # noqa: E402

GOV_PREFIXES = ("RQ", "CRA")


def _f(value):
    try:
        return abs(float(str(value).replace(" ", "").replace(",", ".")))
    except (TypeError, ValueError):
        return None


def _is_gov(cat):
    return bool(cat) and any(cat.startswith(p) for p in GOV_PREFIXES)


def _d(s):
    try:
        return dt.date.fromisoformat(s)
    except (TypeError, ValueError):
        return None


def load_ledger(vault):
    led = V.load_json(os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE), None) or {}
    return led.get("documents", {}) or {}, led.get("obligations", []) or []


def document_amounts(docs):
    out = []
    for doc_id, doc in docs.items():
        for a in doc.get("amounts") or []:
            v = _f(a.get("value"))
            if v is not None:
                out.append({"doc_id": doc_id, "label": a.get("label"), "value": v,
                            "doc_type": doc.get("doc_type"), "period": doc.get("period")})
    return out


def reconcile(vault, date_from, date_to, tol_days, lookback_days):
    P.build(vault)  # idempotent
    docs, obligations = load_ledger(vault)
    amounts = document_amounts(docs)

    payments = [r for r in P.query(vault)
                if _is_gov(r.get("category"))
                and (not date_from or r["date"] >= date_from)
                and (not date_to or r["date"] <= date_to)]
    obligations = [o for o in obligations
                   if (not date_from or (o.get("date") or "") >= date_from)
                   and (not date_to or (o.get("date") or "") <= date_to)]

    by_amount = {}
    for a in amounts:
        by_amount.setdefault(round(a["value"], 2), []).append(a)

    matched, unexplained = [], []
    for p in payments:
        pd_ = _d(p["date"])
        doc_hits = by_amount.get(round(abs(p["amount"]), 2), [])
        obl_hits = [o for o in obligations
                    if _d(o.get("date")) and abs((_d(o["date"]) - pd_).days) <= tol_days]
        if doc_hits or obl_hits:
            matched.append({
                "payment": p,
                "documents": [f"{d['doc_type']} {d['period']}" for d in doc_hits[:3]],
                "obligations": [o.get("id") for o in obl_hits[:3]],
            })
        else:
            unexplained.append(p)

    # heuristic: an obligation is "covered" if some government payment lands within
    # [due - lookback, due + tol]
    uncovered = []
    for o in obligations:
        if o.get("kind") not in ("payment", "instalment", "filing"):
            continue
        due = _d(o.get("date"))
        if not due:
            continue
        near = [p for p in payments
                if -lookback_days <= (_d(p["date"]) - due).days <= tol_days]
        if not near:
            uncovered.append(o)

    return {"vault": vault, "from": date_from, "to": date_to,
            "n_payments": len(payments), "n_obligations": len(obligations),
            "payments": payments, "matched": matched, "unexplained": unexplained,
            "uncovered": uncovered, "totals": _totals(payments),
            "statements": statement_checks(vault, docs)}


def statement_checks(vault, docs):
    """Validate the payments store against each bank statement's (closing - opening)."""
    con_rows = P.query(vault)
    out = []
    for doc in docs.values():
        if doc.get("doc_type") != "BANK-STATEMENT":
            continue
        amt = {a.get("label"): _f(a.get("value")) for a in doc.get("amounts") or []}
        if amt.get("Solde d'ouverture") is None or amt.get("Solde de clôture") is None:
            continue
        m = re.search(r"p[ée]riode (\d{4}-\d{2}-\d{2}) \u2192 (\d{4}-\d{2}-\d{2})", doc.get("notes") or "")
        if not m:
            continue
        start, end = m.group(1), m.group(2)
        net = round(sum(r["amount"] for r in con_rows if start < r["date"] <= end), 2)
        stmt = round(amt["Solde de clôture"] - amt["Solde d'ouverture"], 2)
        out.append({"start": start, "end": end, "statement": stmt, "store": net,
                    "diff": round(stmt - net, 2)})
    return sorted(out, key=lambda x: x["end"])


def _totals(payments):
    out = {}
    for p in payments:
        k = f"{(p.get('category') or '?')} {p['date'][:4]}"
        t = out.setdefault(k, {"n": 0, "total": 0.0})
        t["n"] += 1
        t["total"] = round(t["total"] + p["amount"], 2)
    return dict(sorted(out.items()))


def render(res):
    L = ["# Reconciliation — ledger ↔ bank payments", ""]
    L.append(f"_Window: {res['from'] or 'start'} → {res['to'] or 'today'}. "
             f"{res['n_payments']} government payment(s) in the bank, "
             f"{res['n_obligations']} ledger obligation(s). Generated by `reconcile.py`._")
    L.append("")
    L.append("## A. Government payments in the bank")
    L.append("")
    L.append("| Category | Year | n | Total |")
    L.append("| --- | --- | --- | --- |")
    for k, v in res["totals"].items():
        cat, year = k.rsplit(" ", 1)
        L.append(f"| {cat} | {year} | {v['n']} | {v['total']:,.2f} $ |")
    L.append("")
    L.append("## B. Matched to a ledger amount / dated obligation")
    L.append("")
    if res["matched"]:
        L.append("| Date | Amount | Category | Matched to |")
        L.append("| --- | --- | --- | --- |")
        for m in res["matched"]:
            p = m["payment"]
            to = ", ".join(m["documents"] + m["obligations"]) or "—"
            L.append(f"| {p['date']} | {p['amount']:,.2f} $ | {p.get('category')} | {to} |")
    else:
        L.append("_None._")
    L.append("")
    L.append("## C. Unexplained payments (to a government, matching nothing in the ledger)")
    L.append("")
    L.append("_Most of these mean there is no source document in the ledger for that payment "
             "(an instalment or remittance we have no notice for). Fewer is better._")
    L.append("")
    if res["unexplained"]:
        L.append("| Date | Amount | Category | Description |")
        L.append("| --- | --- | --- | --- |")
        for p in res["unexplained"]:
            desc = ((p.get("description1") or "") + " | " + (p.get("description2") or "")).strip(" |")
            L.append(f"| {p['date']} | {p['amount']:,.2f} $ | {p.get('category')} | {desc} |")
    else:
        L.append("_None._")
    L.append("")
    L.append("## D. Obligations with no government payment near the due date (heuristic)")
    L.append("")
    if res["uncovered"]:
        for o in res["uncovered"]:
            L.append(f"- **{o.get('date')}** {o.get('jurisdiction')} {o.get('doc_type')} — {o.get('label')}")
    else:
        L.append("_None._")
    L.append("")
    L.append("> The bank shows what left the account; only the government **statement of account** "
             "(relevé) shows how each payment was *applied*. Pull it for any period where (A) and "
             "the ledger disagree.")
    L.append("")
    L.append("## E. Bank statement validation (statement balance vs transactions on file)")
    L.append("")
    checks = res.get("statements") or []
    gaps = [c for c in checks if abs(c["diff"]) >= 0.005]
    L.append(f"{len(checks) - len(gaps)} / {len(checks)} statement period(s) reconcile exactly.")
    L.append("")
    if gaps:
        L.append("| Period | Statement Δ | On file Δ | Diff |")
        L.append("| --- | --- | --- | --- |")
        for c in gaps:
            L.append(f"| {c['start']} → {c['end']} | {c['statement']:,.2f} $ | {c['store']:,.2f} $ | {c['diff']:,.2f} $ |")
        L.append("")
        L.append("_A mismatch means a transaction is missing from the imports on file (the bank CSV "
                 "export is lossy) — the statement PDF is the authoritative source for that period._")
    L.append("")
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--from", dest="date_from")
    ap.add_argument("--to", dest="date_to")
    ap.add_argument("--tol-days", type=int, default=3)
    ap.add_argument("--lookback-days", type=int, default=45)
    ap.add_argument("--out")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    vault = os.path.expanduser(args.vault)

    res = reconcile(vault, args.date_from, args.date_to, args.tol_days, args.lookback_days)
    out = os.path.expanduser(args.out) if args.out else os.path.join(vault, V.LEDGER_REL, "reconciliation.md")
    with open(out, "w", encoding="utf-8") as fh:
        fh.write(render(res))

    if args.json:
        print(json.dumps({k: res[k] for k in ("vault", "from", "to", "n_payments",
                                              "n_obligations", "totals")}, indent=2, ensure_ascii=False))
    else:
        print(f"wrote {out}")
        print(f"  government payments : {res['n_payments']}")
        print(f"  matched             : {len(res['matched'])}")
        print(f"  unexplained         : {len(res['unexplained'])}")
        print(f"  possibly unpaid     : {len(res['uncovered'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
