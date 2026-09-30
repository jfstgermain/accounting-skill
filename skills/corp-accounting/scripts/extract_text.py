#!/usr/bin/env python3
"""extract_text.py — dump page-tagged text from an anonymized document.

The model reads this output (not the raw PDF) to extract facts with
page-level provenance. Uses PyMuPDF, already required by the anonymizer.

Usage:
    python3 extract_text.py FILE [--json] [--max-chars N]

Default output is plain text with ``=== page N ===`` markers. With --json it
emits {"file": ..., "pages": [{"n": 1, "text": "..."}, ...]}.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_MAX_CHARS = 40000


def extract(path: str) -> dict:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise SystemExit("PyMuPDF is required: pip install pymupdf")

    doc = fitz.open(path)
    pages = []
    try:
        for i, page in enumerate(doc, start=1):
            pages.append({"n": i, "text": page.get_text("text")})
    finally:
        doc.close()
    return {"file": path, "pages": pages, "page_count": len(pages)}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--max-chars", type=int, default=DEFAULT_MAX_CHARS,
                    help="truncate total text output (default 40000)")
    args = ap.parse_args()

    if not os.path.exists(args.file):
        raise SystemExit(f"file not found: {args.file}")

    data = extract(args.file)
    total = 0
    for p in data["pages"]:
        total += len(p["text"])
        if total > args.max_chars:
            p["text"] = p["text"][: max(0, len(p["text"]) - (total - args.max_chars))]
            p["truncated"] = True
            break

    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=False))
    else:
        print(f"# {data['file']} ({data['page_count']} pages)")
        for p in data["pages"]:
            print(f"\n=== page {p['n']} ===")
            print(p["text"].rstrip())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
