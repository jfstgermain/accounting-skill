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
