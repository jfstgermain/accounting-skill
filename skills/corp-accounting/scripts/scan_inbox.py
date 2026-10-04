#!/usr/bin/env python3
"""scan_inbox.py — list anonymized documents waiting to be processed.

Scans ``30_Anonymized/_Inbox`` recursively, hashes each file, and compares the
hash against ``40_Ledger/processed.json`` so a document is never processed twice.

Usage:
    python3 scan_inbox.py [--vault PATH] [--json] [--out FILE]

Output (stdout, JSON):
    {
      "vault": "...",
      "inbox": "...",
      "new": [ {path, abs_path, name, sha256, size, parsed, naming_ok, issues}, ... ],
      "already_processed": [ ... same shape ... ],
      "errors": [ ... ]
    }

Exit code 0 even when files fail naming validation: the caller (skill) decides.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402


# Subfolders that are the *anonymizer's* category, not a topic to preserve.
CATEGORY_NAMES = {
    "tax", "taxes", "impot", "impots", "impôt", "impôts", "revenu", "revenuqc",
    "revenu-quebec", "rq", "cra", "arc", "federal", "fédéral", "provincial", "avis",
    "releve", "releve-de-compte", "relevé", "relevés", "releves", "bank", "banque",
    "bancaire", "statement", "statements", "transactions", "compte", "tabular", "tableur",
    "csv", "numbers", "table", "donnees", "données",
}


def _topic_for(abs_path: str, inbox: str) -> str | None:
    """First folder under _Inbox, if any, e.g. 'Overpaid REV QC'.

    Category folders produced by the anonymizer (tax/, bank/, …) are not topics.
    """
    rel = os.path.relpath(abs_path, inbox)
    parts = rel.split(os.sep)
    if len(parts) <= 1:
        return None
    return None if parts[0].strip().lower() in CATEGORY_NAMES else parts[0]


def scan(vault: str) -> dict:
    V.ensure_vault(vault)
    inbox = os.path.join(vault, V.INBOX_REL)
    processed = V.load_json(os.path.join(vault, V.LEDGER_REL, V.PROCESSED_FILE), {}) or {}
    result = {"vault": vault, "inbox": inbox, "new": [], "already_processed": [], "errors": []}

    if not os.path.isdir(inbox):
        result["errors"].append(f"inbox not found: {inbox}")
        return result

    for root, _dirs, files in os.walk(inbox):
        for name in sorted(files):
            if name.startswith("."):
                continue
            abs_path = os.path.join(root, name)
            rel = os.path.relpath(abs_path, vault)
            try:
                digest = V.sha256_file(abs_path)
            except OSError as exc:
                result["errors"].append(f"{rel}: {exc}")
                continue
            parsed, issues = V.parse_filename(name)
            entry = {
                "path": rel,
                "abs_path": abs_path,
                "name": name,
                "sha256": digest,
                "size": os.path.getsize(abs_path),
                "parsed": parsed,
                "naming_ok": bool(parsed) and not issues,
                "issues": issues,
                "topic": _topic_for(abs_path, inbox),
            }
            record = processed.get(digest)
            if record:
                entry["processed_at"] = record.get("processed_at")
                entry["file_path"] = record.get("file_path")
                result["already_processed"].append(entry)
            else:
                result["new"].append(entry)
    return result


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT, help="Corp Accounting vault folder")
    ap.add_argument("--json", action="store_true", help="print full JSON (default)")
    ap.add_argument("--out", help="write JSON to this file as well")
    args = ap.parse_args()

    manifest = scan(os.path.expanduser(args.vault))
    payload = json.dumps(manifest, indent=2, ensure_ascii=False)

    if args.out:
        V.save_json(os.path.expanduser(args.out), manifest)

    if args.json or args.out:
        print(payload)
    else:
        print(f"inbox: {manifest['inbox']}")
        print(f"new: {len(manifest['new'])} | already processed: {len(manifest['already_processed'])}")
        for e in manifest["new"]:
            flag = "ok " if e["naming_ok"] else "BAD"
            print(f"  [{flag}] {e['path']}" + ("" if e["naming_ok"] else f"  -> {', '.join(e['issues'])}"))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
