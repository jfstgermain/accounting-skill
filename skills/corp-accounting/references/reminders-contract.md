# Reminders contract

Dated obligations become **Apple Reminders** in the area's list:

| Area | Apple Reminders list |
| --- | --- |
| Corp Accounting | **Accounting - Creatix** |
| Personal Accounting | **Accounting - Personal** |

The list is resolved by `reminders.py` in this order: `--list` → `40_Ledger/reminders_config.json` (`{"list": "..."}`) → the area default above.

## What qualifies

An obligation is pushed when it has:

- a concrete `date` (quoted from the document, with provenance), and
- `kind` ∈ `filing` · `payment` · `instalment` · `response`, and
- a **future** date (past dates are surfaced as tickets instead), and
- a review state of **OK or WARN**.

**REVIEW obligations are never auto-pushed** — they stay human tickets (AGENTS.md rule 7). Use `--include-review` only to override deliberately.

## Command

```bash
python3 scripts/reminders.py push --vault "$VAULT"              # create the reminders
python3 scripts/reminders.py push --vault "$VAULT" --dry-run    # preview, create nothing
```

`push` stages the proposals, then calls the `apple-reminders` CLI (`bin/reminders`) once per proposal and records each key as sent.

## Reminder shape

- **List:** the area's list (table above).
- **Title:** `<jurisdiction label> <doc_type> — <label> (<period>)`, e.g. `Revenu Québec RQ-CO17-ASSESSMENT — paiement (FY2025)`.
- **Due:** the obligation date (all-day).
- **Notes:** the provenance line followed by the hashtags, e.g.
  `30_Anonymized/RevenuQC/2025-10-22_RQ_RQ-CO17-ASSESSMENT_30-AVRIL-2025.pdf (p.1) — « ... »` then `#accounting #RevenuQuebec #CO17 #paiement #FY2025`.

## Tags

Hashtags in the notes become real Apple Reminders tags. They are derived automatically:

- always `#accounting`
- jurisdiction — `#RevenuQuebec` · `#ARC` · `#Paie` · `#Comptable`
- document type — `#CO17` · `#DAS` · `#TPSTVQ` · `#Recouvrement` · `#NonProduction` · `#Remboursement` · `#Releve` · `#Paiement` · `#EtatsFinanciers` · `#Production` · `#Declaration` · `#Correspondance`
- kind — `#paiement` · `#production` · `#acompte` · `#reponse`
- period — `#FY2025` · `#2025`

## Dedup

Proposals are keyed `<doc_id>:<date>:<kind>`. Any key recorded in `40_Ledger/reminders_staged.json["sent"]` is never pushed again, so re-runs are safe.

## Requirements

- the `apple-reminders` skill (`bin/reminders`). Override the path with `REMINDERS_CLI`.
- macOS **Reminders** permission for the app that launches pi — run `reminders doctor`; `fullAccess` is required.

## Rules

- A reminder is a nudge, not a filing: it never files, pays, or remits anything (rule 1).
- Only dates quoted from a document are pushed — never a remembered deadline (rule 2).
- REVIEW items remain tickets; `--include-review` is a deliberate override.
