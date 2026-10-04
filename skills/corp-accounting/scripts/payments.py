#!/usr/bin/env python3
"""payments.py — the derived payments store (SQLite).

Rebuildable index of every bank transaction dropped in ``30_Anonymized/Bank/``. Rows are
deduplicated at the **transaction** level (date + amount + descriptions), not by file
hash, so re-exporting an overlapping bank range never duplicates them.

Canonical data stays the redacted CSV files in the vault; the SQLite file is disposable
and lives **outside** iCloud at ``~/.corp-accounting/<area>.sqlite``.

Usage:
    payments.py build  --vault PATH [--rebuild] [--json]
    payments.py list   --vault PATH [--year YYYY] [--category SUBSTR] [--json]
    payments.py stats  --vault PATH [--json]
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402
import bank_summary as BS  # noqa: E402
import statement_txns as ST  # noqa: E402

DB_DIR = os.path.expanduser("~/.corp-accounting")


def slug(vault: str) -> str:
    base = os.path.basename(vault.rstrip("/")) or "vault"
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def db_path(vault: str) -> str:
    return os.path.join(DB_DIR, f"{slug(vault)}.sqlite")


def connect(vault: str, rebuild: bool = False) -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    path = db_path(vault)
    if rebuild and os.path.exists(path):
        os.remove(path)
    con = sqlite3.connect(path)
    con.execute("""
        CREATE TABLE IF NOT EXISTS bank_files (
            sha256 TEXT PRIMARY KEY, path TEXT, imported_at TEXT, rows_total INTEGER)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS payments (
            fingerprint TEXT PRIMARY KEY,
            date TEXT, amount REAL, direction TEXT,
            description1 TEXT, description2 TEXT,
            category TEXT, ref TEXT,
            source TEXT, source_file TEXT, imported_at TEXT)
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(payments)")}
    if "source" not in cols:
        con.execute("ALTER TABLE payments ADD COLUMN source TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS ix_pay_date ON payments(date)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_pay_cat ON payments(category)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return con


def _meta_get(con, key):
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else None


def _meta_set(con, key, value):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, value))


def current_source(vault):
    if not os.path.exists(db_path(vault)):
        return None
    con = connect(vault)
    src = _meta_get(con, "active_source")
    con.close()
    return src


def ensure_built(vault, rules=None):
    """Build only if the store does not exist yet; never mix sources implicitly."""
    if not os.path.exists(db_path(vault)):
        return build(vault, False, rules, "auto")
    return None


def _classify(text: str, rules: dict | None = None) -> str | None:
    for name, pats in (rules or BS.DEFAULT_RULES).items():
        if any(re.search(p, text, re.I) for p in pats):
            return name
    return None


def _ref(text: str) -> str | None:
    m = re.search(r"\b([A-Z]{3,6})\s+(\d{5,})\b", text)
    return f"{m.group(1)} {m.group(2)}" if m else None


def parse_bank_csv(path: str, rules: dict | None = None):
    """Return a list of normalized transaction dicts from one bank export."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return []
    header, data = rows[0], [r for r in rows[1:] if any(c.strip() for c in r)]
    date_i, amt_i, desc_i = BS.find_cols(header)
    if date_i is None or amt_i is None:
        raise ValueError(f"{os.path.basename(path)}: cannot find date/amount columns: {header}")
    order = BS.detect_order([r[date_i] for r in data if len(r) > date_i])
    out = []
    for r in data:
        if len(r) <= max(date_i, amt_i):
            continue
        d = BS.parse_date(r[date_i], order)
        amount = BS.to_float(r[amt_i])
        if d is None or amount is None:
            continue
        d1 = (r[desc_i[0]] if desc_i and len(r) > desc_i[0] else "").strip()
        d2 = (r[desc_i[-1]] if len(desc_i) > 1 and len(r) > desc_i[-1] else "").strip()
        text = f"{d1} {d2}"
        fp = hashlib.sha1(f"{d.isoformat()}|{amount:.2f}|{d1}|{d2}".encode()).hexdigest()
        out.append({"fingerprint": fp, "date": d.isoformat(), "amount": amount,
                    "direction": "out" if amount < 0 else "in",
                    "description1": d1, "description2": d2,
                    "category": _classify(text, rules), "ref": _ref(text)})
    return out


def _norm(s):
    return re.sub(r"\s+", " ", (s or "").strip()).upper()


def _insert(con, rows, source, source_file, now, rules=None):
    """Insert rows, deduping on (date|amount|description); repeats get a suffix."""
    seen = {}
    added = 0
    for t in rows:
        desc = _norm(t.get("description") or (t.get("description1", "") + " " + t.get("description2", "")))
        base = hashlib.sha1(f"{t['date']}|{t['amount']:.2f}|{desc}".encode()).hexdigest()
        seen[base] = seen.get(base, 0) + 1
        fp = base if seen[base] == 1 else f"{base}#{seen[base]}"
        text = f"{t.get('description') or t.get('description1','')} {t.get('description2','')}"
        cur = con.execute(
            "INSERT OR IGNORE INTO payments (fingerprint,date,amount,direction,description1,"
            "description2,category,ref,source,source_file,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (fp, t["date"], t["amount"], "out" if t["amount"] < 0 else "in",
             t.get("description") or t.get("description1", ""), t.get("description2", ""),
             t.get("category") or _classify(text, rules), t.get("ref"), source, source_file, now))
        added += cur.rowcount
    return added


STATEMENT_EXT = ("BANK-STATEMENT",)


def build(vault: str, rebuild: bool = False, rules: dict | None = None,
          source: str = "auto") -> dict:
    V.ensure_vault(vault)
    bank = os.path.join(vault, V.ANON_REL, "Bank")
    con = connect(vault, rebuild)
    now = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    files, added, seen = 0, 0, 0

    statements = ST.statements_in(vault) if source in ("auto", "statements") else []
    if source == "auto":
        source = "statements" if statements else "csv"

    # a store holds ONE source: statements and CSV descriptions differ, so their
    # fingerprints never collide — mixing them would double-count. Switching source
    # therefore purges and rebuilds.
    stored = _meta_get(con, "active_source")
    switched = bool(stored) and stored != source
    if switched:
        con.execute("DELETE FROM payments")
        con.execute("DELETE FROM bank_files")
    _meta_set(con, "active_source", source)

    if source == "statements":
        for path in statements:
            rel = os.path.relpath(path, vault)
            digest = V.sha256_file(path)
            if con.execute("SELECT 1 FROM bank_files WHERE sha256=?", (digest,)).fetchone():
                seen += 1
                continue
            _period, txns = ST.parse_statement_transactions(path)
            added += _insert(con, txns, "statement", rel, now, rules)
            con.execute("INSERT OR REPLACE INTO bank_files VALUES (?,?,?,?)",
                        (digest, rel, now, len(txns)))
            files += 1

    elif os.path.isdir(bank):
        for name in sorted(os.listdir(bank)):
            if not name.lower().endswith((".csv", ".tsv")):
                continue
            path = os.path.join(bank, name)
            digest = V.sha256_file(path)
            if con.execute("SELECT 1 FROM bank_files WHERE sha256=?", (digest,)).fetchone():
                seen += 1
                continue
            try:
                txns = parse_bank_csv(path, rules)
            except Exception as exc:  # noqa: BLE001
                con.close()
                raise SystemExit(f"parse failed: {exc}")
            added += _insert(con, txns, "csv", os.path.relpath(path, vault), now)
            con.execute("INSERT OR REPLACE INTO bank_files VALUES (?,?,?,?)",
                        (digest, os.path.relpath(path, vault), now, len(txns)))
            files += 1
    con.commit()
    total = con.execute("SELECT count(*) FROM payments").fetchone()[0]
    con.close()
    return {"vault": vault, "db": db_path(vault), "source": source, "switched": switched,
            "files_imported": files, "files_skipped": seen, "rows_added": added,
            "rows_total": total}


def query(vault: str, year: str | None = None, category: str | None = None) -> list[dict]:
    con = connect(vault)
    sql = "SELECT date,amount,description1,description2,category,ref,source_file,source FROM payments WHERE 1=1"
    args = []
    if year:
        sql += " AND substr(date,1,4)=?"
        args.append(year)
    if category:
        sql += " AND category LIKE ?"
        args.append(f"%{category}%")
    sql += " ORDER BY date"
    rows = [{"date": r[0], "amount": r[1], "description1": r[2], "description2": r[3],
             "category": r[4], "ref": r[5], "source_file": r[6], "source": r[7]} for r in con.execute(sql, args)]
    con.close()
    return rows


def stats(vault: str) -> list[dict]:
    con = connect(vault)
    rows = [{"category": r[0], "year": r[1], "n": r[2], "total": round(r[3], 2)}
            for r in con.execute(
                "SELECT COALESCE(category,'uncategorised'), substr(date,1,4), count(*), sum(amount) "
                "FROM payments GROUP BY 1,2 ORDER BY 2,1")]
    unimported = [r[0] for r in con.execute("SELECT path FROM bank_files ORDER BY path")]
    con.close()
    return {"by_category_year": rows, "bank_files": unimported}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["build", "list", "stats"])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--rules", help="JSON rules file overriding the payment categories")
    ap.add_argument("--source", choices=["auto", "csv", "statements"], default="auto",
                    help="where transactions come from (auto: statements when available, else the CSV)")
    ap.add_argument("--year")
    ap.add_argument("--category")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    vault = os.path.expanduser(args.vault)

    if args.cmd == "build":
        rules = json.load(open(os.path.expanduser(args.rules), encoding="utf-8")) if args.rules else None
        res = build(vault, args.rebuild, rules, args.source)
        print(json.dumps(res, indent=2) if args.json else
              f"db: {res['db']}\nsource: {res['source']}"
              + ("  (source changed -> store purged)" if res.get("switched") else "")
              + f"\nimported {res['files_imported']} file(s), "
              f"skipped {res['files_skipped']} already-imported, added {res['rows_added']} new row(s), "
              f"{res['rows_total']} total")
        return 0
    if args.cmd == "list":
        rows = query(vault, args.year, args.category)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for r in rows:
                print(f"  {r['date']}  {r['amount']:>12.2f}  {r['category'] or '-':20} "
                      f"{(r['description1'] + ' | ' + r['description2'])[:60]}")
            print(f"{len(rows)} row(s)")
        return 0
    if args.cmd == "stats":
        res = stats(vault)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            for r in res["by_category_year"]:
                print(f"  {r['category']:20} {r['year']}  n={r['n']:>3}  {r['total']:>12.2f}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
