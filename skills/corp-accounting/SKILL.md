---
name: corp-accounting
description: Process anonymized corporate accounting documents for a Quebec CCPC. Drops in 30_Anonymized/_Inbox are scanned, hashed, deduplicated, text-extracted, and turned into provenance-carrying facts (dates, amounts, references), a deterministic compliance ledger, and an OK/WARN/REVIEW review queue; documents are sorted into jurisdiction folders and Apple Reminders are staged (never auto-created); draft reports land in 50_Reports. Use when asked to process the corp accounting inbox, run the accounting pipeline, file anonymized CRA/Revenu Quebec documents, update the compliance ledger, or review corporate deadlines.
---

# Corp Accounting (Canada / Quebec CCPC)

Turns a drop of **anonymized** corporate documents into structured, provenance-carrying
records: extraction JSON, a compliance ledger, a forced review queue, staged reminders,
and draft reports.

## Golden rules (inherit from the vault AGENTS.md)

1. **Never file, pay, remit, or submit anything to CRA or Revenu Québec.** Never send email.
2. **Never state a tax rate, threshold, deadline, or balance from memory.** Use a date or
   amount only if it is quoted from the document, with page + snippet.
3. **Only read `Corp Accounting/30_Anonymized/`.** Never read raw/source documents,
   `20_Extracted` JSON through a cloud model, mappings, or secrets. (Extraction may run on a
   local model or a deterministic parser; this skill's text extraction is local.)
4. **Every extracted fact carries provenance:** source file, page, short snippet. Never guess
   a number — flag it as REVIEW.
5. **The accountant is the source of truth.** This skill drafts; a human reviews and sends.
6. **Never auto-act on a REVIEW item.** The queue is OK / WARN / REVIEW + human tickets only.
7. **Reminders are staged, then confirmed.** The script proposes; a human approves; only then
   are they created.

## Paths

- Vault: `~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Finance/Corp Accounting`
- Drop zone: `30_Anonymized/_Inbox/` (subfolders `CRA/ RevenuQC/ Payroll/ Accountant/ _Unsorted/`)
- Scripts: the `scripts/` folder next to this SKILL.md

Set once for the session:

```bash
VAULT="$HOME/Library/Mobile Documents/iCloud~md~obsidian/Documents/Finance/Corp Accounting"
SKILL="$HOME/dev/accounting-skill/skills/corp-accounting"
```

## Pipeline

Run the steps in order. Extraction (step 3) happens **before** filing (step 5), because the
ledger must record a document before it leaves `_Inbox`.

### 1. Scan the inbox

```bash
python3 "$SKILL/scripts/scan_inbox.py" --vault "$VAULT" --out /tmp/corp-scan.json
```

Each new item carries its sha256, parsed filename, and `naming_ok`. Anything already in
`40_Ledger/processed.json` is skipped automatically.

### 2. Triage non-conforming filenames

If `naming_ok` is false, the jurisdiction cannot be trusted (anonymization removed the
BN/NEQ). Do **not** guess. Extract what you can, mark `jurisdiction: "Unknown"` and
`confidence: "REVIEW"`; it will be filed to `_Unsorted/` and raised as a ticket.

### 3. Extract text, then facts

Dump page-tagged text locally (PyMuPDF; no network, no cloud):

```bash
python3 "$SKILL/scripts/extract_text.py" "$VAULT/30_Anonymized/_Inbox/<file>.pdf"
```

Read that text and write **one** extracted-document JSON per file to
`20_Extracted/<sha256>.json`, using the contract in `references/doc-types.md`.

**Optional accelerator.** For RQ/CRA forms, `prefill.py` drafts the JSON by pairing known
labels with their values (page + snippet provenance), then you review and correct it:

```bash
python3 "$SKILL/scripts/prefill.py" --vault "$VAULT"                 # drafts from _Inbox
python3 "$SKILL/scripts/prefill.py" --vault "$VAULT" --dir "$VAULT/30_Anonymized/RevenuQC"
```

Fields it cannot resolve are left out and the draft stays `confidence: WARN`. Always review
before trusting — it is a deterministic draft, not an authority.

The JSON carries:

- `jurisdiction`, `doc_type`, `period`, `issued_date`
- `dates[]` — every filing/payment/instalment/response date, each with `page` + `snippet`
- `amounts[]` and `references[]` — each with `page` + `snippet`
- `confidence` — `OK` / `WARN` / `REVIEW`
- `doc_id` = the file's sha256, `source_file` = the inbox-relative path

If a value is not legible in the text, leave it out and set `confidence: "REVIEW"`.

### 4. Upsert into the ledger

```bash
python3 "$SKILL/scripts/ledger.py" upsert --vault "$VAULT" --extracted "20_Extracted/<sha256>.json"
```

This validates the JSON, computes the deterministic review state, rebuilds obligations, and
records the document in `processed.json`.

### 5. File the documents

```bash
python3 "$SKILL/scripts/file_docs.py" --vault "$VAULT"
```

Moves processed files into `30_Anonymized/<JURIS>/` (or `_Unsorted/`), handles name
collisions, and keeps ledger provenance in sync. Files not yet processed are skipped and
reported. A file whose name does not follow the convention but whose extracted
`jurisdiction` is known is **renamed to the convention** and filed by content; the original
name is kept in `processed.json`. Only a genuinely unknown jurisdiction goes to `_Unsorted/`.

### 6. Render the review queue

```bash
python3 "$SKILL/scripts/ledger.py" review --vault "$VAULT"
```

Writes `40_Ledger/review_queue.md` (documents, obligations, tickets). Present OK/WARN/REVIEW
to the user as human review tickets.

### 7. Stage reminders

```bash
python3 "$SKILL/scripts/reminders.py" stage --vault "$VAULT"
```

Prints proposals (deduplicated by document + date + kind). **Present them and ask.** On
confirmation, create them with the `apple-reminders` skill in a list named "Corp Accounting",
then record each:

```bash
python3 "$SKILL/scripts/reminders.py" mark --vault "$VAULT" --key "<key>"
```

Only dates quoted from a document are ever proposed.

### 8. Draft reports

Via the `obsidian` CLI, write to `50_Reports/`:

- `Corp Accounting Review YYYY-MM-DD.md` — what was processed, obligations ahead, tickets
- Append open questions to `50_Reports/Questions for the accountant.md`

See `references/report-contracts.md`. Never state a tax position as fact; route it to the
accountant as a question.

## Idempotency

Re-running is safe. `processed.json` (keyed by sha256) skips documents already handled, so
the ledger and reminders do not duplicate. Use `--dry-run` on `file_docs.py` to preview moves.

## Verify before trusting

```bash
python3 "$SKILL/scripts/selftest.py"
```

## References

- `references/data-pipeline.md` — folders, naming, hashing, ordering, state files
- `references/doc-types.md` — jurisdiction/type taxonomy and the extraction JSON contract
- `references/review-queue.md` — OK / WARN / REVIEW rules and ticket language
- `references/reminders-contract.md` — staging, dedup, and confirmation flow
- `references/report-contracts.md` — report/note frontmatter and templates

## Scripts

- `scripts/scan_inbox.py` — hashes new inbox files, validates names, dedups vs processed.json
- `scripts/extract_text.py` — local page-tagged text dump (PyMuPDF)
- `scripts/prefill.py` — deterministic draft extraction for RQ/CRA forms (review before use)
- `scripts/ledger.py` — upsert extracted docs; render review_queue.md
- `scripts/file_docs.py` — sort processed docs into jurisdiction folders
- `scripts/reminders.py` — stage reminder proposals; mark created
- `scripts/selftest.py` — end-to-end test on a throwaway vault
- `scripts/vaultlib.py` — shared helpers (paths, naming, hashing, review rules)
