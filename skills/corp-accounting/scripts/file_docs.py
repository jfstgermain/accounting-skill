#!/usr/bin/env python3
"""file_docs.py — sort processed anonymized documents into jurisdiction folders.

Only documents already recorded in ``40_Ledger/processed.json`` are moved, so a
file is never filed before it has been extracted. Files whose name does not
match the convention go to ``30_Anonymized/_Unsorted`` (and surface as REVIEW in
the queue). The final path is written back to ``processed.json``.

Usage:
    python3 file_docs.py [--vault PATH] [--manifest FILE] [--dry-run] [--json]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402
import scan_inbox  # noqa: E402


def file_documents(vault: str, manifest: dict | None = None, dry_run: bool = False) -> dict:
    V.ensure_vault(vault)
    if manifest is None:
        manifest = scan_inbox.scan(vault)

    processed_path = os.path.join(vault, V.LEDGER_REL, V.PROCESSED_FILE)
    processed = V.load_json(processed_path, {}) or {}

    moves, skipped = [], []
    for e in manifest["new"] + manifest["already_processed"]:
        record = processed.get(e["sha256"])
        if not record:
            skipped.append({"path": e["path"], "reason": "not processed yet"})
            continue

        if e["naming_ok"] and e["parsed"] and e["parsed"].get("juris"):
            dest_dir = os.path.join(vault, V.ANON_REL, e["parsed"]["juris"])
        else:
            dest_dir = os.path.join(vault, V.UNSORTED_REL)

        dest = os.path.join(dest_dir, e["name"])
        if os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(e["abs_path"]):
            base, ext = os.path.splitext(e["name"])
            dest = os.path.join(dest_dir, f"{base}__{e['sha256'][:8]}{ext}")

        rel_dest = os.path.relpath(dest, vault)
        moves.append({"from": e["path"], "to": rel_dest, "sha256": e["sha256"],
                      "naming_ok": e["naming_ok"]})
        if not dry_run:
            os.makedirs(dest_dir, exist_ok=True)
            shutil.move(e["abs_path"], dest)
            record["file_path"] = rel_dest

    if not dry_run and processed:
        V.save_json(processed_path, processed)

    # keep ledger provenance in sync with the final location
    if not dry_run and moves:
        ledger_path = os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE)
        ledger = V.load_json(ledger_path, None)
        if ledger and ledger.get("documents"):
            for m in moves:
                doc = ledger["documents"].get(m["sha256"])
                if doc:
                    doc["source_file"] = m["to"]
                    doc["file_path"] = m["to"]
            V.save_json(ledger_path, ledger)

    return {"vault": vault, "dry_run": dry_run, "moved": moves, "skipped": skipped}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--vault", default=V.DEFAULT_VAULT)
    ap.add_argument("--manifest", help="scan_inbox JSON output to consume")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    manifest = V.load_json(os.path.expanduser(args.manifest)) if args.manifest else None
    result = file_documents(os.path.expanduser(args.vault), manifest, args.dry_run)

    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        prefix = "(dry-run) " if result["dry_run"] else ""
        print(f"{prefix}moved: {len(result['moved'])} | skipped: {len(result['skipped'])}")
        for m in result["moved"]:
            print(f"  {m['from']}  ->  {m['to']}")
        for s in result["skipped"]:
            print(f"  SKIP {s['path']} ({s['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
