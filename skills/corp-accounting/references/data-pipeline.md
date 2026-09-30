# Data pipeline

## Folders and flow

```
raw / source documents        (outside the vault — anonymized manually)
        │
        ▼  anonymizer (manual)
30_Anonymized/_Inbox/         drop zone
        │  scan_inbox.py  → hash, dedup, validate name
        ▼
20_Extracted/<sha256>.json    one file per document, with provenance
        │  ledger.py upsert
        ▼
40_Ledger/
  processed.json              sha256 manifest (idempotency)
  ledger.json                 documents + obligations
  review_queue.md             rendered OK / WARN / REVIEW + tickets
  reminders_staged.json       proposed reminders + sent keys
        │  file_docs.py
        ▼
30_Anonymized/<JURIS>/        sorted; _Unsorted/ when the name is bad
```

`50_Reports/` holds human outputs. `100_Docs/` holds vault/tooling documentation.

## Naming convention (the sort key)

```
YYYY-MM-DD_<JURIS>_<TYPE>_<PERIOD>[_rev<N>][_anon].<ext>
```

- `<JURIS>` ∈ `CRA` · `RQ` / `RevenuQC` · `Payroll` · `Accountant`
- a trailing `_anon` (added by the anonymizer's `--suffix`) is ignored
- allowed extensions: pdf, png, jpg, jpeg, tif, tiff

Examples: `2026-06-15_RQ_DAS_2026-05.pdf`, `2026-03-31_CRA_T2-ASSESSMENT_2025.pdf`,
`2026-04-30_RQ_CO17_2025.pdf`.

**Why the name matters:** after anonymization the BN/NEQ/name are redacted, so jurisdiction
and type cannot be recovered from the content. The filename is the only reliable sort key.
When it does not conform, the document goes to `_Unsorted/` and raises a REVIEW ticket.

## Hashing and idempotency

`scan_inbox.py` computes a sha256 per file. `processed.json` maps
`sha256 → {name, original_path, file_path, processed_at, doc_id}`. A document whose hash is
present is skipped on subsequent scans, so re-runs never duplicate ledger rows or reminders.

Two byte-identical files share a hash and are treated as one document (deliberate).

## Ordering guarantee

Extraction (step 3) must complete and `ledger.py upsert` must run **before** `file_docs.py`,
because `file_docs.py` only moves documents already present in `processed.json`. A file that
fails extraction stays in `_Inbox` and is reported as skipped — it surfaces instead of
silently disappearing.

## Provenance

Every date, amount, and reference carries `page` + `snippet`. `file_docs.py` rewrites the
ledger document's `source_file`/`file_path` to the final sorted location so provenance stays
valid after the move.
