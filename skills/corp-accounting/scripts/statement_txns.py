#!/usr/bin/env python3
"""statement_txns.py — extract transactions from bank-statement PDFs.

The statements are the authoritative record, so the payments store can be rebuilt from
them instead of a (possibly incomplete) CSV export. Amounts are right-aligned into
debit / credit / balance columns, so each amount word is assigned to the column whose
header is nearest to its right edge — that is what distinguishes a debit from a credit.

Works for the RBC business wording ("Chèques et débits" / "Dépôts et crédits") and the
RBC personal wording ("Retraits" / "Dépôts").

Usage:
    statement_txns.py PDF [--json]           # dump one statement's transactions
    statement_txns.py --vault PATH [--json]  # every statement in 30_Anonymized/Bank
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
import bank_statements as BS  # noqa: E402

MONTHS = {"janv": 1, "févr": 2, "fevr": 2, "mars": 3, "avr": 4, "mai": 5, "juin": 6, "juil": 7,
          "août": 8, "aout": 8, "sept": 9, "oct": 10, "nov": 11, "déc": 12, "dec": 12}
AMOUNT = re.compile(r"^[\d ]*\d,\d{2}$")
TOKEN = re.compile(r"^\[[A-Z]+-?\d*\]$")
COLUMNS = ("debit", "credit", "balance")


def _columns(page):
    """Header y and {column: reference_right_edge} for the transaction table."""
    by_y = {}
    for w in page.get_text("words"):
        by_y.setdefault(round(w[1]), []).append(w)
    for y in sorted(by_y):
        row = by_y[y]
        text = " ".join(w[4] for w in row).lower()
        if ("débits" in text or "retraits" in text) and "solde" in text and "($)" in text:
            right = sorted((w for w in row if w[0] > 300), key=lambda w: w[0])
            if not right:
                continue
            groups, cur = [], [right[0]]
            for w in right[1:]:
                if w[0] - cur[-1][2] > 22:
                    groups.append(cur); cur = [w]
                else:
                    cur.append(w)
            groups.append(cur)
            if len(groups) >= 3:
                return y, {c: max(w[2] for w in g) for c, g in zip(COLUMNS, groups[:3])}
    return None, None


def _infer_year(day, month, start, end):
    for year in (end.year, start.year, end.year - 1):
        try:
            cand = dt.date(year, month, day)
        except ValueError:
            continue
        if start <= cand <= end:
            return cand.isoformat()
    return None


def parse_statement_transactions(path, period=None):
    """Return (period, [ {date, description, amount, balance} ])."""
    import pymupdf
    info = BS.parse_statement(path)
    if period is None:
        period = info.get("period")
    if not period or not period[0] or not period[1]:
        return None, []
    start = dt.date.fromisoformat(period[0])
    end = dt.date.fromisoformat(period[1])

    doc = pymupdf.open(path)
    txs, current, pending, cols, header_y = [], None, "", None, None
    for page in doc:
        hy, c = _columns(page)
        if c:
            cols, header_y = c, hy
        if not cols:
            continue
        rows = {}
        for w in page.get_text("words"):
            if w[1] <= header_y:
                continue
            rows.setdefault(round(w[1]), []).append(w)
        for y in sorted(rows):
            row = sorted(rows[y], key=lambda w: w[0])
            m = re.match(r"^(\d{1,2})$", row[0][4]) if row else None
            if m and len(row) > 1 and row[1][4].lower().rstrip(".") in MONTHS:
                day = int(m.group(1))
                mon = MONTHS[row[1][4].lower().rstrip(".")]
                dd = _infer_year(day, mon, start, end)
                if dd:
                    current = dd
            desc = " ".join(w[4] for w in row if 60 < w[0] < 300 and not TOKEN.match(w[4])).strip()
            bucket = {}
            for w in row:
                if w[0] <= 300 or TOKEN.match(w[4]):
                    continue
                col = min(cols, key=lambda k: abs(w[2] - cols[k]))
                bucket.setdefault(col, []).append(w[4])
            amounts = {c: "".join(v) for c, v in bucket.items() if AMOUNT.match("".join(v))}
            if desc:
                pending = desc
            if "debit" in amounts or "credit" in amounts:
                if "credit" in amounts:
                    value = float(amounts["credit"].replace(" ", "").replace(",", "."))
                else:
                    value = -float(amounts["debit"].replace(" ", "").replace(",", "."))
                txs.append({"date": current, "description": pending or desc,
                            "amount": round(value, 2), "balance": amounts.get("balance")})
                pending = ""
    doc.close()
    return period, txs


def statements_in(vault):
    """Every BANK-STATEMENT PDF recorded in the ledger, with its parsed period."""
    ledger = V.load_json(os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE), None) or {}
    out = []
    for doc in (ledger.get("documents") or {}).values():
        if doc.get("doc_type") != "BANK-STATEMENT":
            continue
        src = doc.get("file_path") or doc.get("source_file")
        if src and os.path.exists(os.path.join(vault, src)):
            out.append(os.path.join(vault, src))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("pdf", nargs="?")
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.pdf:
        period, txs = parse_statement_transactions(os.path.expanduser(args.pdf))
        if args.json:
            print(json.dumps({"period": period, "transactions": txs}, indent=2, ensure_ascii=False))
        else:
            print(f"period {period[0]} → {period[1]} · {len(txs)} transaction(s)")
            for t in txs:
                print(f"  {t['date']}  {t['amount']:>12.2f}  {t['description'][:56]}")
            print(f"  net: {sum(t['amount'] for t in txs):,.2f}")
        return 0

    vault = os.path.expanduser(args.vault)
    total, bad = 0, []
    out = []
    for path in statements_in(vault):
        period, txs = parse_statement_transactions(path)
        if not txs:
            bad.append(os.path.basename(path)); continue
        net = round(sum(t["amount"] for t in txs), 2)
        stmt = None
        info = BS.parse_statement(path)
        if info["figures"].get("Solde d'ouverture") is not None and info["figures"].get("Solde de clôture") is not None:
            stmt = round(info["figures"]["Solde de clôture"] - info["figures"]["Solde d'ouverture"], 2)
        total += len(txs)
        out.append({"file": os.path.basename(path), "period": period, "n": len(txs),
                    "net": net, "statement": stmt, "ok": stmt is None or abs(net - stmt) < 0.005})
    if args.json:
        print(json.dumps({"statements": out, "transactions": total}, indent=2, ensure_ascii=False))
    else:
        for o in out:
            flag = "" if o["ok"] else "  <-- MISMATCH"
            print(f"  {o['period'][0]} → {o['period'][1]}  n={o['n']:>3}  net={o['net']:>12,.2f}"
                  f"  stmt={o['statement'] if o['statement'] is not None else '?':>12}{flag}")
        print(f"{total} transaction(s) across {len(out)} statement(s); unparsed: {bad or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
