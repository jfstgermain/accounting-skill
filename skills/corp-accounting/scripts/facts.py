#!/usr/bin/env python3
"""facts.py — the derived facts store (SQLite).

Loads the extracted-document facts already merged into ``40_Ledger/ledger.json``
into queryable ``documents`` and ``facts`` tables, alongside ``payments`` (built by
``payments.py``) in the same per-area SQLite file.

The DB is a **disposable, rebuildable index**. Canonical data stays the
``20_Extracted/*.json`` + ``40_Ledger/ledger.json`` records in the vault; this
script only mirrors them, one-way, with full provenance (source_file, page,
snippet) and the extractor's confidence / review state on every row.

Boundary: personal facts land in ``~/.corp-accounting/personal-accounting.sqlite``,
corporate facts in ``~/.corp-accounting/corp-accounting.sqlite`` — never mixed.

Usage:
    facts.py build  --vault PATH [--rebuild] [--json]
    facts.py list   --vault PATH [--doc-type T] [--label SUB] [--line N] [--period YYYY] [--json]
    facts.py lines  --vault PATH [--period YYYY] [--json]   # income-line facts only
    facts.py summary --vault PATH [--json]
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402

DB_DIR = os.path.expanduser("~/.corp-accounting")


def slug(vault: str) -> str:
    base = os.path.basename(vault.rstrip("/")) or "vault"
    return re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")


def db_path(vault: str) -> str:
    return os.path.join(DB_DIR, f"{slug(vault)}.sqlite")


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def connect(vault: str) -> sqlite3.Connection:
    os.makedirs(DB_DIR, exist_ok=True)
    con = sqlite3.connect(db_path(vault))
    con.execute("""
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY,
            doc_type TEXT, jurisdiction TEXT, period TEXT, issued_date TEXT,
            confidence TEXT, review_state TEXT, topic TEXT,
            source_file TEXT, notes TEXT, imported_at TEXT)
    """)
    con.execute("""
        CREATE TABLE IF NOT EXISTS facts (
            fact_id TEXT PRIMARY KEY,
            doc_id TEXT,
            kind TEXT,          -- 'amount' | 'reference' | 'date'
            label TEXT,
            value TEXT,
            value_num REAL,     -- numeric value when parseable, else NULL
            line TEXT,          -- tax line number (e.g. '101', '12010') when known
            year TEXT,          -- fiscal/tax year when a fact spans columns
            page INTEGER,
            snippet TEXT,
            imported_at TEXT)
    """)
    cols = {r[1] for r in con.execute("PRAGMA table_info(facts)")}
    if "year" not in cols:
        con.execute("ALTER TABLE facts ADD COLUMN year TEXT")
    con.execute("CREATE INDEX IF NOT EXISTS ix_facts_doc ON facts(doc_id)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_facts_label ON facts(label)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_facts_line ON facts(line)")
    con.execute("CREATE INDEX IF NOT EXISTS ix_docs_type ON documents(doc_type)")
    con.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT)")
    return con


def to_num(value) -> float | None:
    """Best-effort numeric parse of a fact value string (en/fr separators)."""
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    s = str(value).replace("\u00a0", "").replace("\u202f", "").replace(" ", "")
    if not s:
        return None
    s = re.sub(r"[^0-9.,+\-]", "", s)
    if not s:
        return None
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    try:
        return float(s)
    except ValueError:
        return None


def _fact_id(doc_id: str, kind: str, label: str, value: str, line, year, page) -> str:
    raw = "|".join([doc_id, kind, label or "", value or "", str(line or ""),
                    str(year or ""), str(page or "")])
    return hashlib.sha1(raw.encode()).hexdigest()


def _load_ledger(vault: str) -> dict:
    path = os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE)
    data = V.load_json(path, None) or {}
    return data.get("documents", {})


def build(vault: str, rebuild: bool = False) -> dict:
    V.ensure_vault(vault)
    con = connect(vault)
    now = _now()
    if rebuild:
        con.execute("DELETE FROM facts")
        con.execute("DELETE FROM documents")
    docs = _load_ledger(vault)
    n_docs = n_facts = 0
    for doc_id, doc in docs.items():
        con.execute(
            "INSERT OR REPLACE INTO documents "
            "(doc_id,doc_type,jurisdiction,period,issued_date,confidence,review_state,"
            "topic,source_file,notes,imported_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (doc_id, doc.get("doc_type"), doc.get("jurisdiction"), doc.get("period"),
             doc.get("issued_date"), str(doc.get("confidence", "OK")),
             doc.get("review_state", "OK"), doc.get("topic"), doc.get("source_file"),
             doc.get("notes"), now))
        n_docs += 1
        # per-doc replace so incremental builds never leave orphaned facts
        con.execute("DELETE FROM facts WHERE doc_id=?", (doc_id,))
        entries = []
        for a in doc.get("amounts") or []:
            entries.append(("amount", a.get("label"), a.get("value"),
                            a.get("line"), a.get("year"), a.get("page"), a.get("snippet")))
        for r in doc.get("references") or []:
            entries.append(("reference", r.get("label"), r.get("value"),
                            r.get("line"), r.get("year"), r.get("page"), r.get("snippet")))
        for d in doc.get("dates") or []:
            entries.append(("date", d.get("label"), d.get("date"),
                            d.get("line"), d.get("year"), d.get("page"), d.get("snippet")))
        for kind, label, value, line, year, page, snippet in entries:
            con.execute(
                "INSERT OR REPLACE INTO facts "
                "(fact_id,doc_id,kind,label,value,value_num,line,year,page,snippet,imported_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (_fact_id(doc_id, kind, label, value, line, year, page),
                 doc_id, kind, label, value, to_num(value), line, year, page, snippet, now))
            n_facts += 1
    con.execute("INSERT OR REPLACE INTO meta VALUES ('facts_updated_at',?)", (now,))
    con.commit()
    total_docs = con.execute("SELECT count(*) FROM documents").fetchone()[0]
    total_facts = con.execute("SELECT count(*) FROM facts").fetchone()[0]
    con.close()
    return {"vault": vault, "db": db_path(vault), "documents": n_docs,
            "facts": n_facts, "documents_total": total_docs, "facts_total": total_facts}


def _query(vault: str, doc_type=None, label=None, line=None, period=None, kind=None,
          year=None, distinct: bool = False) -> list[dict]:
    con = connect(vault)
    sql = ("SELECT f.kind, f.label, f.value, f.value_num, f.line, f.year, f.page, f.snippet, "
           "d.doc_type, d.jurisdiction, d.period, d.issued_date, d.source_file, d.review_state "
           "FROM facts f JOIN documents d ON d.doc_id = f.doc_id WHERE 1=1")
    args: list = []
    if doc_type:
        sql += " AND d.doc_type LIKE ?"
        args.append(f"%{doc_type}%")
    if label:
        sql += " AND f.label LIKE ?"
        args.append(f"%{label}%")
    if line:
        sql += " AND f.line = ?"
        args.append(str(line))
    if year:
        sql += " AND f.year = ?"
        args.append(str(year))
    if period:
        sql += " AND d.period LIKE ?"
        args.append(f"%{period}%")
    if kind:
        sql += " AND f.kind = ?"
        args.append(kind)
    sql += " ORDER BY d.period, f.year, f.line, f.label"
    rows = [{"kind": r[0], "label": r[1], "value": r[2], "value_num": r[3], "line": r[4],
             "year": r[5], "page": r[6], "snippet": r[7], "doc_type": r[8], "jurisdiction": r[9],
             "period": r[10], "issued_date": r[11], "source_file": r[12], "review_state": r[13]}
            for r in con.execute(sql, args)]
    con.close()
    if distinct:
        seen, out = set(), []
        for r in rows:
            k = (r["label"], r["value_num"] if r["value_num"] is not None else r["value"],
                 r.get("year"))
            if k not in seen:
                seen.add(k)
                out.append(r)
        rows = out
    return rows


def summary(vault: str) -> dict:
    con = connect(vault)
    by_type = [{"doc_type": r[0], "n": r[1]} for r in con.execute(
        "SELECT doc_type, count(*) FROM documents GROUP BY doc_type ORDER BY 1")]
    by_kind = [{"kind": r[0], "n": r[1]} for r in con.execute(
        "SELECT kind, count(*) FROM facts GROUP BY kind ORDER BY 1")]
    con.close()
    return {"db": db_path(vault), "by_doc_type": by_type, "by_kind": by_kind}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("cmd", choices=["build", "list", "lines", "summary"])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--rebuild", action="store_true")
    ap.add_argument("--doc-type")
    ap.add_argument("--label")
    ap.add_argument("--line")
    ap.add_argument("--year")
    ap.add_argument("--period")
    ap.add_argument("--kind")
    ap.add_argument("--distinct", action="store_true",
                    help="one row per (label, year, value) — collapses duplicate docs/columns")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    vault = os.path.expanduser(args.vault)

    if args.cmd == "build":
        res = build(vault, args.rebuild)
        print(json.dumps(res, indent=2) if args.json else
              f"db: {res['db']}\n{res['documents']} doc(s), {res['facts']} fact(s) "
              f"({res['documents_total']}/{res['facts_total']} total in store)")
        return 0
    if args.cmd in ("list", "lines"):
        kind = None if args.cmd == "list" else "amount"
        rows = _query(vault, args.doc_type, args.label, args.line, args.period, kind, args.year,
                      args.distinct)
        if args.json:
            print(json.dumps(rows, indent=2, ensure_ascii=False))
        else:
            for r in rows:
                y = f"  [{r['year']}]" if r.get("year") else ""
                print(f"  {r['period'] or '?':>8}  {r['doc_type'] or '-':16} "
                      f"{r['line'] or '-':>6}  {r['label'] or '-':40} = {r['value'] or '-'}{y}")
            print(f"{len(rows)} row(s)")
        return 0
    if args.cmd == "summary":
        res = summary(vault)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            print(f"db: {res['db']}")
            for r in res["by_doc_type"]:
                print(f"  {r['doc_type'] or '-':18} {r['n']:>4}")
            for r in res["by_kind"]:
                print(f"  {r['kind']:10} {r['n']:>4}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
