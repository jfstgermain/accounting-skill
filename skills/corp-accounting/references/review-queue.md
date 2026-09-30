# Forced review queue

Deterministic states, computed in `vaultlib.review_state_for()` and rendered by
`ledger.py review` into `40_Ledger/review_queue.md`. The queue never asserts that anything
was filed, paid, remitted, or submitted — it flags what a human must look at.

## States

| State | Meaning | Trigger |
| --- | --- | --- |
| `OK` | Fully legible, provenance present, type/jurisdiction clear | default |
| `WARN` | Needs a look, not blocking | no dated obligation found; extractor `confidence: WARN` |
| `REVIEW` | A required fact is missing or untrusted | see below |

## REVIEW triggers

- `jurisdiction` missing or `Unknown`
- `doc_type` missing or `Unknown`
- a `dates[]` entry without a `date`
- a `dates[]` entry without `page` or `snippet` (provenance gap)
- an `amounts[]` entry without `page` or `snippet`
- extractor `confidence: REVIEW`

## Obligations

Every `dates[]` entry becomes an obligation row:

```
id = "<doc_id>:<date>:<kind>"
```

sorted by date, then kind (`filing < payment < instalment < response < other`). The obligation
inherits its document's review state.

## Ticket language

When presenting tickets to the user:

- describe the gap, not a verdict — "date entry lacks provenance", not "invalid"
- never convert a past date into "overdue" or "unpaid"; say "date has passed — confirm status"
- route tax positions to the accountant as questions, never as conclusions

## Never auto-act

A REVIEW item is never acted upon (no filing, no payment, no reminder without confirmation,
no report asserting a tax position). It becomes a human ticket and stops there.
