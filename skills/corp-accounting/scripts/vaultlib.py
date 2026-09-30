#!/usr/bin/env python3
"""vaultlib.py — shared helpers for the corp-accounting skill.

No third-party dependencies. Imported by the sibling scripts; they add this
file's directory to sys.path so it can be imported when run directly.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import re

DEFAULT_VAULT = os.path.expanduser(
    "~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Finance/Corp Accounting"
)

INBOX_REL = "30_Anonymized/_Inbox"
UNSORTED_REL = "30_Anonymized/_Unsorted"
ANON_REL = "30_Anonymized"
EXTRACTED_REL = "20_Extracted"
LEDGER_REL = "40_Ledger"

LEDGER_FILE = "ledger.json"
PROCESSED_FILE = "processed.json"
REVIEW_FILE = "review_queue.md"
REMINDERS_FILE = "reminders_staged.json"

# Canonical jurisdiction folder for each accepted JURIS token in the filename.
JURIS_DIRS = {
    "CRA": "CRA",
    "REVENUQC": "RevenuQC",
    "RQ": "RevenuQC",
    "PAYROLL": "Payroll",
    "ACCOUNTANT": "Accountant",
}

# Canonical jurisdiction folder -> token used in a normalised filename.
JURIS_TOKENS = {"CRA": "CRA", "RevenuQC": "RQ", "Payroll": "Payroll", "Accountant": "Accountant"}

# YYYY-MM-DD_<JURIS>_<TYPE>_<PERIOD>[_rev<N>][_anon].ext
NAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})_"
    r"(?P<juris>[A-Za-z]+)_"
    r"(?P<type>[A-Za-z0-9][A-Za-z0-9.-]*)_"
    r"(?P<period>[A-Za-z0-9][A-Za-z0-9.-]*)"
    r"(?:_rev(?P<rev>\d+))?"
    r"(?:_anon)?"
    r"\.(?P<ext>[A-Za-z0-9]+)$"
)

ALLOWED_EXT = {"pdf", "png", "jpg", "jpeg", "tif", "tiff"}

VALID_STATES = ("OK", "WARN", "REVIEW")


def sha256_file(path: str, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def load_json(path: str, default=None):
    if not os.path.exists(path):
        return default
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def save_json(path: str, data) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2, ensure_ascii=False, sort_keys=False)
        fh.write("\n")
    os.replace(tmp, path)


def parse_filename(name: str):
    """Return (parsed_dict, issues_list). parsed_dict is None if unparseable."""
    m = NAME_RE.match(name)
    if not m:
        return None, ["filename does not match YYYY-MM-DD_<JURIS>_<TYPE>_<PERIOD>[_rev<N>].ext"]
    g = m.groupdict()
    issues = []
    ext = (g["ext"] or "").lower()
    if ext not in ALLOWED_EXT:
        issues.append(f"unexpected extension .{ext}")
    token = (g["juris"] or "").upper()
    juris = JURIS_DIRS.get(token)
    if juris is None:
        issues.append(f"unknown jurisdiction token '{g['juris']}' (expected CRA / RQ / RevenuQC / Payroll / Accountant)")
    parsed = {
        "date": g["date"],
        "juris_token": g["juris"],
        "juris": juris,
        "type": g["type"],
        "period": g["period"],
        "rev": g["rev"],
        "ext": ext,
    }
    return parsed, issues


def canon_juris_dir(juris: str | None):
    if not juris:
        return None
    return JURIS_DIRS.get(juris.upper()) or JURIS_DIRS.get(juris) or None


def sanitize_token(value: str | None, default: str = "UNKNOWN") -> str:
    """Uppercase, hyphenated token safe for the filename convention."""
    if not value:
        return default
    cleaned = re.sub(r"[^A-Za-z0-9.]+", "-", str(value).strip()).strip("-.")
    return cleaned.upper() or default


def conventional_name(juris: str, date: str | None, doc_type: str | None,
                      period: str | None, ext: str = "pdf") -> str | None:
    """Build a conforming filename from extracted fields; None if too incomplete."""
    token = JURIS_TOKENS.get(juris)
    if not token or not date:
        return None
    return f"{date}_{token}_{sanitize_token(doc_type)}_{sanitize_token(period)}.{ext.lower()}"


def review_state_for(doc: dict) -> tuple[str, list[str]]:
    """Deterministic OK/WARN/REVIEW for one extracted document.

    Returns (state, tickets). Never asserts that something was filed or paid;
    it only flags what needs a human to look.
    """
    tickets: list[str] = []
    state = "OK"

    def bump(new: str):
        nonlocal state
        order = {"OK": 0, "WARN": 1, "REVIEW": 2}
        if order[new] > order[state]:
            state = new

    juris = doc.get("jurisdiction")
    if not juris or juris == "Unknown":
        tickets.append("jurisdiction is unknown")
        bump("REVIEW")
    if not doc.get("doc_type") or doc.get("doc_type") == "Unknown":
        tickets.append("document type is unknown")
        bump("REVIEW")

    dates = doc.get("dates") or []
    actionable_types = {"RQ-COLLECTION", "RQ-NON-PRODUCTION", "RQ-REFUND-HOLD"}
    informational_types = {"RQ-STATEMENT", "RQ-PAYMENT"}
    nonzero = any((a.get("value") not in (None, "0.00", "0", "-0.00")) for a in (doc.get("amounts") or []))
    needs_action = (doc.get("doc_type") in actionable_types
                    or (nonzero and doc.get("doc_type") not in informational_types))
    if not dates and needs_action:
        tickets.append("no dated obligation found — confirm whether action is required")
        bump("WARN")
    today = _dt.date.today().isoformat()
    for d in dates:
        if not d.get("date"):
            tickets.append(f"date entry without a date: {d.get('label', '?')}")
            bump("REVIEW")
        if not d.get("page") or not d.get("snippet"):
            tickets.append(f"date entry lacks provenance (page+snippet): {d.get('label', d.get('date', '?'))}")
            bump("REVIEW")
        if d.get("date") and d["date"] < today:
            tickets.append(f"date {d['date']} has passed — confirm status (do not assume filed/paid)")
            bump("WARN")

    for a in doc.get("amounts") or []:
        if not a.get("page") or not a.get("snippet"):
            tickets.append(f"amount lacks provenance (page+snippet): {a.get('label', '?')}")
            bump("REVIEW")

    if str(doc.get("confidence", "OK")).upper() == "REVIEW":
        tickets.append("extractor confidence is REVIEW")
        bump("REVIEW")
    elif str(doc.get("confidence", "OK")).upper() == "WARN":
        bump("WARN")

    return state, tickets


def ensure_vault(vault: str) -> None:
    if not os.path.isdir(vault):
        raise SystemExit(f"vault folder not found: {vault}")
