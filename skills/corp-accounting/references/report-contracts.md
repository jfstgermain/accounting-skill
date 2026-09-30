# Report contracts

Human-readable outputs in `50_Reports/`, written through the `obsidian` CLI in the Finance
vault. Keep frontmatter consistent with the rest of the vault.

## Review report

`50_Reports/Corp Accounting Review YYYY-MM-DD.md`

```markdown
---
tags:
  - finance/corp-accounting
  - corp-accounting/reports
type: report
status: draft
updated: YYYY-MM-DD
---
# Corp Accounting Review — YYYY-MM-DD

Processed this run: N document(s). Review queue: OK x · WARN y · REVIEW z.

## Handled
| Date filed | Jurisdiction | Type | Period | State |
| --- | --- | --- | --- | --- |

## Obligations ahead
| Date | Kind | Jurisdiction | Type | Label | Source |
| --- | --- | --- | --- | --- | --- |

## Tickets (WARN / REVIEW)
- **REVIEW** — `<source_file>` — <gap description>

## Open questions for the accountant
- <question, no tax position asserted>

> Draft only. Nothing here has been filed, paid, or remitted.
```

## Questions for the accountant

`50_Reports/Questions for the accountant.md` — a running list, appended via `obsidian append`.

- one bullet per question, each referencing the source document
- never assert a tax position; phrase as a question
- mark items resolved with a date rather than deleting them

## Language rules

- No tax rate, threshold, deadline, or balance unless quoted from a document (with page).
- Never state that something was filed/paid/remitted.
- The accountant signs off; this is a draft.
- Keep personal and corporate material separate.
