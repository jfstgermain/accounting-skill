#!/usr/bin/env python3
"""redact_table.py — redact PII from tabular exports (CSV / TSV / Apple Numbers).

Uses the same identity config as the PDF anonymizer (`~/my_identity.json`) and a
built-in pattern set that mirrors the tax/bank profiles. Writes a redacted copy
plus a value->token mapping *outside* the output file (so the mapping can never
be uploaded by accident).

Supported inputs: .csv, .tsv, .txt (delimited), .numbers (Apple Numbers, via the
optional `numbers-parser` package).

Usage:
    python3 redact_table.py INPUT [--config ~/my_identity.json] [--out PATH]
        [--suffix _redacted] [--mapping-dir DIR] [--longnum-min 12]
        [--dry-run] [--json]

Exit codes: 0 ok · 2 residual PII found in the output · 4 error.
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import sys

APOS = "['\u2019]"
SEP = "[\\s\\-\u2019']+"  # space / hyphen / apostrophe are equivalent separators
NUMERIC_CATEGORIES = {"SIN", "BN", "TVQ", "NEQ", "PHONE", "POSTAL", "ACCOUNT", "LONGNUM"}

# label-anchored first, then standalone formats
PATTERNS = [
    ("SIN", re.compile(
        r"(?i)(?<![A-Za-z])(?:social\s+insurance\s+number|num[ée]ro\s+d" + APOS +
        r"assurance\s+sociale|NAS|SIN)\b[\s:.#/-]{0,15}"
        r"([0-9]{3}[ .\-]?[0-9]{3}[ .\-]?[0-9]{3})(?![\d])"), 1),
    ("BN", re.compile(
        r"(?i)(?<![A-Za-z])(?:business\s+number|num[ée]ro\s+d" + APOS +
        r"entreprise|N[°o]\s*(?:d" + APOS + r")?entreprise|BN)\b[\s:.#/-]{0,15}"
        r"([0-9]{9}(?:\s?[A-Z]{2}[0-9]{4})?)(?![\d])"), 1),
    ("TVQ", re.compile(r"(?i)(?<![A-Za-z])TVQ\b[\s:.#/-]{0,15}([0-9]{10}(?:\s?TQ[0-9]{4})?)(?![\d])"), 1),
    ("NEQ", re.compile(
        r"(?i)(?<![A-Za-z])(?:NEQ|num[ée]ro\s+d" + APOS +
        r"entreprise\s+du\s+Qu[ée]bec)\b[\s:.#/-]{0,15}([0-9]{10})(?![\d])"), 1),
    ("PHONE", re.compile(r"(?<![\d(])(\(?\d{3}\)?[ .\-]\d{3}[ .\-]\d{4})(?!\d)"), 1),
    ("EMAIL", re.compile(r"([A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,})"), 1),
    ("POSTAL", re.compile(
        r"(?<![A-Za-z0-9])([ABCEGHJKLMNPRSTVXY][0-9][ABCEGHJKLMNPRSTVWXYZ]\s?[0-9]"
        r"[ABCEGHJKLMNPRSTVWXYZ][0-9])(?![A-Za-z0-9])", re.I), 1),
]

LITERAL_BUCKETS = {"persons": "PERSON", "orgs": "ORG", "addresses": "ADDRESS", "extra": "EXTRA", "accounts": "ACCOUNT"}


def flexible_literal_regex(value: str) -> re.Pattern:
    """Case-insensitive literal where spaces, hyphens and apostrophes are equivalent
    separators — so 'Jean-Francois St-Germain' matches 'JEAN-FRANCOIS ST GERMAIN'."""
    parts = [re.escape(p) for p in re.split(SEP, value.strip()) if p]
    return re.compile(SEP.join(parts), re.IGNORECASE)


class Registry:
    def __init__(self):
        self.by_key: dict[tuple[str, str], str] = {}
        self.counts: dict[str, int] = {}

    def token(self, category: str, value: str) -> str:
        key_val = re.sub(r"\s+", "", value).casefold() if category in NUMERIC_CATEGORIES else re.sub(r"\s+", " ", value).strip().casefold()
        key = (category, key_val)
        if key not in self.by_key:
            self.counts[category] = self.counts.get(category, 0) + 1
            self.by_key[key] = f"[{category}-{self.counts[category]}]"
        return self.by_key[key]


def build_literals(config: dict):
    out = []
    for bucket, prefix in LITERAL_BUCKETS.items():
        for entry in config.get(bucket, []):
            value = entry.get("value") if isinstance(entry, dict) else entry
            token = entry.get("token") if isinstance(entry, dict) else None
            if value:
                out.append((token or prefix, flexible_literal_regex(str(value)), str(value)))
    out.sort(key=lambda t: len(t[2]), reverse=True)  # longest first
    return out


def redact_text(text: str, literals, patterns, registry: Registry, longnum_min: int | None):
    if not isinstance(text, str) or not text:
        return text
    for token, rx, value in literals:
        def _sub(m, value=value, token=token):
            return token if token.startswith("[") else registry.token(token, value)
        text = rx.sub(_sub, text)
    for category, rx, group in patterns:
        text = rx.sub(lambda m: registry.token(category, m.group(group)), text)
    if longnum_min:
        rx = re.compile(rf"(?<![\d.])(\d{{{longnum_min},}})(?![\d.])")
        text = rx.sub(lambda m: registry.token("LONGNUM", m.group(1)), text)
    return text


def _read_rows(path: str):
    if path.lower().endswith(".numbers"):
        try:
            from numbers_parser import Document
        except ImportError:
            raise SystemExit("numbers-parser required for .numbers: pip install numbers-parser")
        doc = Document(path)
        table = doc.sheets[0].tables[0]
        rows = [["" if c is None else str(c) for c in r] for r in table.rows(values_only=True)]
        return rows, ","
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(8192)
        fh.seek(0)
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t")
        except csv.Error:
            dialect = csv.excel
        return [row for row in csv.reader(fh, dialect)], dialect.delimiter


def redact_file(path: str, config_path: str, out_path: str | None, suffix: str,
                mapping_dir: str, longnum_min: int | None, dry_run: bool, encoding: str = "utf-8"):
    rows, delim = _read_rows(path)
    config = json.load(open(os.path.expanduser(config_path), encoding="utf-8")) if config_path else {}
    literals = build_literals(config)
    registry = Registry()

    out_rows = [[redact_text(cell, literals, PATTERNS, registry, longnum_min) for cell in row] for row in rows]

    base, _ext = os.path.splitext(path)
    if out_path is None:
        out_path = f"{base}{suffix}.csv"
    out_path = os.path.expanduser(out_path)

    residual = verify(out_rows, literals, PATTERNS, longnum_min)

    if not dry_run:
        os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
        with open(out_path, "w", newline="", encoding=encoding) as fh:
            writer = csv.writer(fh, delimiter=",")
            writer.writerows(out_rows)
        if registry.by_key:
            os.makedirs(mapping_dir, exist_ok=True)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            mpath = os.path.join(mapping_dir, f"{os.path.basename(base)}_{stamp}.mapping.json")
            with open(mpath, "w", encoding="utf-8") as fh:
                json.dump({
                    "created": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
                    "warning": "Contains original PII. NEVER upload or share this file.",
                    "source": os.path.abspath(path),
                    "output": os.path.abspath(out_path),
                    "counts": registry.counts,
                    "values": {v: t for (c, v), t in registry.by_key.items()},
                }, fh, indent=2, ensure_ascii=False)
            os.chmod(mpath, 0o600)

    return {"source": path, "output": out_path, "rows": len(out_rows),
            "counts": registry.counts, "residual": residual, "dry_run": dry_run}


def verify(rows, literals, patterns, longnum_min) -> list[str]:
    findings = []
    longnum_rx = re.compile(rf"(?<![\d.])(\d{{{longnum_min},}})(?![\d.])") if longnum_min else None
    for i, row in enumerate(rows):
        text = " | ".join(c for c in row if isinstance(c, str))
        for _token, rx, _v in literals:
            if rx.search(text):
                findings.append(f"row {i}: configured literal still present")
        for category, rx, _g in patterns:
            if rx.search(text):
                findings.append(f"row {i}: {category} pattern still present")
        if longnum_rx and longnum_rx.search(text):
            findings.append(f"row {i}: long numeric run still present")
    return findings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("input")
    ap.add_argument("--config", default=os.path.expanduser("~/my_identity.json"))
    ap.add_argument("--out")
    ap.add_argument("--suffix", default="_redacted")
    ap.add_argument("--mapping-dir", default=os.path.expanduser("~/.pdf-anonymizer/mappings"))
    ap.add_argument("--longnum-min", type=int, default=12,
                    help="redact standalone digit runs of this length or more (0 = off)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if not os.path.exists(args.input):
        raise SystemExit(f"input not found: {args.input}")

    result = redact_file(args.input, args.config, args.out, args.suffix,
                         os.path.expanduser(args.mapping_dir),
                         args.longnum_min or None, args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"{'DRY-RUN ' if result['dry_run'] else ''}rows: {result['rows']}  tokens: {result['counts'] or '{}'}")
        print(f"output : {result['output']}")
        if result["residual"]:
            print(f"RESIDUAL PII ({len(result['residual'])}):")
            for f in result["residual"][:10]:
                print("  -", f)
        else:
            print("verify : clean")

    return 2 if result["residual"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
