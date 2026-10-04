# Tabular data (CSV / Apple Numbers)

Bank and accounting exports are not PDFs — the PDF anonymizer cannot touch them, and a
`.numbers` file is a proprietary zip/IWA archive, not a spreadsheet you can read as text.
**Never place a raw export in the vault.** Raw exports live outside (in this setup:
`Proton Drive/Accounting/1. Creatix/1. Relevés/Bank/`). Use `redact_table.py` to make a
cloud-safe copy.

## Flow

1. **Raw export stays outside the vault.**

2. **Redact** into the vault:

   ```bash
   python3 scripts/redact_table.py "…/Bank/bank-transactions.csv" \
       --out "$VAULT/30_Anonymized/Bank/bank-transactions_redacted.csv"
   ```

   - reuses the same identity config as the PDF anonymizer (`~/my_identity.json`:
     `persons`, `orgs`, `addresses`, `extra`, `accounts`)
   - **flexible separators**: a config literal `Jean-Francois St-Germain` matches
     `JEAN-FRANCOIS ST GERMAIN` (space/hyphen/apostrophe treated as equivalent), which the
     PDF tool does not do — this is what catches split/uppercased name fields
   - patterns: SIN, BN, TVQ, NEQ, phone, email, postal code; standalone digit runs of
     `--longnum-min` (default 12) are treated as account/reference numbers
   - writes the value→token mapping **outside** the output (under
     `~/.pdf-anonymizer/mappings/`, mode 0600) and **verifies** the result
     (exit code 2 if any residual is found)
   - inputs: `.csv`, `.tsv`, delimited `.txt`, and `.numbers` (needs `numbers-parser`)

3. **Summarize / reconcile** the redacted copy:

   ```bash
   python3 scripts/bank_summary.py REDACTED.csv [--category RQ-income-tax]
   python3 scripts/bank_summary.py REDACTED.csv --match expected.json
   ```

   - auto-detects the date / amount / description columns and the date order
     (`M/D` vs `D/M`; bank exports here are `M/D/YYYY`)
   - classifies rows with regex rules (default: RQ sales tax / source deductions /
     income tax / other, CRA, RQ refund) and totals by category and year
   - `--match expected.json` checks a list of expected payments
     (`[{"label","date","amount"}, …]`, ±3 days and ±0.01) and reports missing ones

## Rules

- Redaction is **literal + pattern**, not semantic: a name that is not in the config, or a
  differently-spelled entity, still passes through. Review before sharing.
- The mapping file is local-only — never upload or commit it.
- Keep raw exports outside the vault; only the redacted copy belongs in `30_Anonymized/`.
- Dates: confirm the export's date order before trusting any reconciliation.

## Bank reconciliation (payments store + `reconcile.py`)

Bank exports are a *ledger*, not a document, so they are stored with **transaction-level**
identity (not by file hash) — re-exporting an overlapping range never duplicates rows.

```bash
# 1. (re)build the derived payments store from 30_Anonymized/Bank/*.csv
python3 scripts/payments.py build --vault "$VAULT"
python3 scripts/payments.py build --vault "$VAULT" --rules "$VAULT/40_Ledger/payment_rules.json"
python3 scripts/payments.py stats --vault "$VAULT"

# 2. reconcile the ledger against the bank
python3 scripts/reconcile.py --vault "$VAULT" [--from YYYY-MM-DD] [--to YYYY-MM-DD]
```

The store lives **outside** iCloud at `~/.corp-accounting/<area>.sqlite`; the redacted CSVs
stay canonical and the DB is disposable (`build --rebuild`).

`reconcile.py` writes `40_Ledger/reconciliation.md`: (A) government payments by category and
year, (B) matches against ledger amounts/obligations, (C) unexplained payments — usually a
payment with no source document in the ledger, (D) obligations with no payment near the due
date. It **cannot** see how the government *applied* a payment — only the statement of account
(relevé) shows that, so pull it whenever (A) and the ledger disagree.

Classification rules are per-area: drop a `40_Ledger/payment_rules.json`
(`{"Category": ["regex", …]}`) to tune them — the personal bank describes RQ payments as
`… - NNNN RE`, which the defaults don't catch.

## Reconciliation cadence

- **Monthly** — import the bank export; catches a missed or misapplied payment early.
- **Quarterly** — bank **+ RQ/CRA statements of account** (aligns with the instalment and
  GST/QST cycles).
- **On receipt** — every assessment/notice (it drives the obligation + reminder).
- **Year-end** (after Apr 30) — full three-way reconciliation before the accountant handoff.
