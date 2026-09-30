#!/usr/bin/env python3
"""bank_summary.py — summarize / reconcile a bank transactions export.

Reads a (redacted) CSV of transactions, classifies rows with regex rules, totals
by category and year, and optionally checks that a list of expected payments is
present in the data. Prints no counterparty names by default; it works on
amounts, dates and the rule that matched.

Usage:
    python3 bank_summary.py INPUT.csv [--rules rules.json] [--category NAME]
        [--match expected.json] [--date-order auto|MDY|DMY] [--json]

Rules file format: {"Category": ["regex", ...], ...}   (first match wins)
Match file format: [{"label": "...", "date": "YYYY-MM-DD", "amount": 1234.56}, ...]
Exit codes: 0 ok · 1 some expected items missing · 4 error.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

DEFAULT_RULES = {
    "RQ-sales-tax": [r"TVQPS"],
    "RQ-source-deductions": [r"\bRAS\b", r"EMPTX"],
    "RQ-income-tax": [r"DECOR", r"REV\s*QC\s*CODE\s*PAI"],
    "RQ-other": [r"TXINS", r"\bRQ\b"],
    "CRA": [r"DPA\s*ADRC", r"\bADRC\b", r"\bARC\b"],
    "RQ-refund": [r"GOUV\.?\s*QUEBEC"],
}


def find_cols(header: list[str]):
    low = [h.lower() for h in header]
    date = next((i for i, h in enumerate(low) if "date" in h), None)
    amt = next((i for i, h in enumerate(low) if "cad" in h or "amount" in h or "montant" in h), None)
    if amt is None:
        amt = next((i for i, h in enumerate(low) if "$" in h), None)
    desc = [i for i, h in enumerate(low) if "description" in h or "libell" in h]
    return date, amt, desc


def detect_order(dates: list[str]) -> str:
    first_max = second_max = 0
    for d in dates:
        m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", d or "")
        if m:
            first_max = max(first_max, int(m.group(1)))
            second_max = max(second_max, int(m.group(2)))
    if first_max > 12:
        return "DMY"
    if second_max > 12:
        return "MDY"
    return "MDY"  # ambiguous: default to month-first (bank exports here are M/D/YYYY)


def parse_date(d: str, order: str):
    m = re.match(r"(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})", d or "")
    if not m:
        m2 = re.match(r"(\d{4})-(\d{2})-(\d{2})", d or "")
        if m2:
            return dt.date(int(m2.group(1)), int(m2.group(2)), int(m2.group(3)))
        return None
    a, b, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if y < 100:
        y += 2000
    mo, da = (a, b) if order == "MDY" else (b, a)
    try:
        return dt.date(y, mo, da)
    except ValueError:
        return None


def to_float(s):
    if s is None:
        return None
    t = re.sub(r"[^\d.\-]", "", str(s))
    if t in ("", "-", "."):
        return None
    try:
        return float(t)
    except ValueError:
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    ap.add_argument("--rules")
    ap.add_argument("--category")
    ap.add_argument("--match")
    ap.add_argument("--tolerance-days", type=int, default=3)
    ap.add_argument("--date-order", choices=["auto", "MDY", "DMY"], default="auto")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    rules = DEFAULT_RULES
    if args.rules:
        rules = json.load(open(os.path.expanduser(args.rules), encoding="utf-8"))
    compiled = [(name, [re.compile(p, re.I) for p in pats]) for name, pats in rules.items()]

    with open(os.path.expanduser(args.input), newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        raise SystemExit("empty input")
    header, data = rows[0], rows[1:]
    date_i, amt_i, desc_i = find_cols(header)
    if date_i is None or amt_i is None:
        raise SystemExit(f"could not find date/amount columns in header: {header}")

    order = args.date_order
    if order == "auto":
        order = detect_order([r[date_i] for r in data if len(r) > date_i])

    tx = []
    for r in data:
        if len(r) <= max(date_i, amt_i):
            continue
        d = parse_date(r[date_i], order)
        v = to_float(r[amt_i])
        text = " ".join(r[i] for i in (desc_i or []) if len(r) > i)
        cat = next((name for name, pats in compiled if any(p.search(text) for p in pats)), None)
        tx.append({"date": d, "amount": v, "category": cat, "text": text})

    by_cat = {}
    for t in tx:
        if not t["category"] or t["amount"] is None:
            continue
        c = by_cat.setdefault(t["category"], {"n": 0, "total": 0.0, "by_year": {}})
        c["n"] += 1
        c["total"] += t["amount"]
        if t["date"]:
            y = str(t["date"].year)
            c["by_year"][y] = round(c["by_year"].get(y, 0.0) + t["amount"], 2)

    result = {"file": args.input, "date_order": order, "rows": len(tx), "categories": by_cat}

    matched = missing = None
    if args.match:
        expected = json.load(open(os.path.expanduser(args.match), encoding="utf-8"))
        matched, missing = [], []
        for e in expected:
            want = dt.date.fromisoformat(e["date"]) if e.get("date") else None
            amt = abs(float(e["amount"]))
            hit = None
            for t in tx:
                if t["amount"] is None:
                    continue
                if abs(abs(t["amount"]) - amt) < 0.01 and want and t["date"] and abs((t["date"] - want).days) <= args.tolerance_days:
                    hit = t
                    break
            (matched if hit else missing).append({**e, "bank": str(hit["date"]) if hit else None})
        result["matched"], result["missing"] = matched, missing

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    else:
        print(f"file: {args.input}  ({result['rows']} rows, date order {order})")
        for name, c in sorted(by_cat.items(), key=lambda kv: kv[1]["total"]):
            print(f"  {name:22} n={c['n']:>3}  total={c['total']:>12.2f}  by year {c['by_year']}")
        if args.category:
            sel = args.category
            print(f"\nrows in {sel}:")
            for t in sorted([t for t in tx if t["category"] == sel], key=lambda t: (t["date"] or dt.date.min)):
                print(f"  {t['date']}  {t['amount']:>12.2f}  {t['text'][:60]}")
        if matched is not None:
            print(f"\nmatch: {len(matched)} ok / {len(missing)} missing")
            for m in missing:
                print(f"  MISSING {m.get('label','')} {m.get('date','')} {m.get('amount','')}")

    return 1 if (missing) else 0


if __name__ == "__main__":
    raise SystemExit(main())
