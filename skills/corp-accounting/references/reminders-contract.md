# Reminders contract

Reminders are **staged, never auto-created**. The script proposes; a human confirms; only
then does the agent create anything.

## What qualifies

A reminder is proposed only for an obligation that has:

- a concrete `date` (quoted from the document, with provenance), and
- `kind` ∈ `filing` · `payment` · `instalment` · `response`

`other` kinds and obligations without a date are not proposed.

## Dedup key

```
key = "<doc_id>:<date>:<kind>"
```

A key already recorded in `reminders_staged.json["sent"]` is never proposed again — re-runs
are safe.

## Flow

1. Stage:

   ```bash
   python3 scripts/reminders.py stage --vault "$VAULT"
   ```

   Writes `40_Ledger/reminders_staged.json` and prints proposals. Nothing is created.

2. Present the proposals to the owner and ask for confirmation. State the source file, page,
   and snippet for each so the owner can verify.

3. On confirmation, create each reminder with the **apple-reminders** skill:

   - list: `Corp Accounting`
   - title: the proposal `title`
   - due date: the proposal `date`
   - notes: `source: <source_file> p.<page> — "<snippet>"`

4. Record each created reminder:

   ```bash
   python3 scripts/reminders.py mark --vault "$VAULT" --key "<key>"
   ```

## Rules

- Never create a reminder for a date that is not in a document (no remembered deadlines).
- Never create reminders for REVIEW items without an explicit human decision.
- If the owner declines a proposal, do not mark it; leave it staged so it is not silently lost.
- A reminder is a nudge, not a filing. It never replaces a human action toward CRA/RQ.
