# Document types and the extraction contract

## Jurisdictions

| `<JURIS>` | Meaning | Typical types |
| --- | --- | --- |
| `CRA` | Federal | T2, T2 assessment/notice, GST/HST return & remittance, PD7A, NOA, correspondence |
| `RQ` / `RevenuQC` | Quebec | CO-17, CO-17 assessment, QST return & remittance, DAS (source deductions), correspondence |
| `Payroll` | Payroll-specific | DAS / PD7A confirmations and summaries |
| `Accountant` | From/to the accountant | working papers, filed returns, emails |
| `Unknown` | Unreadable/unclear | — always yields REVIEW |

Type is free-form but keep it short and stable (uppercase, hyphenated). Do not invent
deadlines or rates; record only what the document states.

## Extraction JSON contract

One file per anonymized document, written to `20_Extracted/<sha256>.json`:

```json
{
  "doc_id": "<sha256 of the source file>",
  "source_file": "30_Anonymized/_Inbox/2026-05-12_CRA_GST-REMITTANCE_2026-04.pdf",
  "jurisdiction": "CRA",
  "doc_type": "GST-REMITTANCE",
  "period": "2026-04",
  "issued_date": "2026-05-12",
  "dates": [
    {"label": "GST remittance due", "date": "2026-06-15", "kind": "payment",
     "page": 1, "snippet": "Payment due June 15, 2026"}
  ],
  "amounts": [
    {"label": "Net tax", "value": "1234.56", "page": 1, "snippet": "Net tax 1,234.56"}
  ],
  "references": [
    {"label": "Return period", "value": "2026-04", "page": 1, "snippet": "Period: April 2026"}
  ],
  "confidence": "OK",
  "notes": "free text"
}
```

### Field rules

- `dates[].kind` ∈ `filing` · `payment` · `instalment` · `response` · `other`.
- `snippet` is a short verbatim quote from the page (a few words around the value).
- `amounts`/`references` values are **strings** exactly as shown (keep separators).
- `confidence`:
  - `OK` — all required facts legible with provenance
  - `WARN` — a fact is present but ambiguous, or a non-critical field is missing
  - `REVIEW` — a required fact is missing/unreadable, or the type/jurisdiction is unclear
- `doc_id` **must** equal the file's sha256 (from `scan_inbox.py`).

If a value is not legible, omit it and lower `confidence` — never guess.

## Validation

`ledger.py upsert` requires `doc_id`, `jurisdiction`, `doc_type`, `dates`, and each date to
carry `date`, `kind`, `page`, `snippet`. Missing provenance → REVIEW ticket.
