# accounting-skill

AI skill + agent for processing **anonymized** corporate accounting documents for a Quebec
CCPC (Créatix), inside an Obsidian vault. Companion to `finance-assistant` (personal
portfolio) but deliberately separate: personal and corporate books do not share a context.

## What it does

A drop of anonymized documents in `30_Anonymized/_Inbox/` becomes:

- `20_Extracted/<sha256>.json` — one extracted-document record per file, every fact with
  source file, page, and snippet
- `40_Ledger/` — `processed.json` (sha256 manifest), `ledger.json` (documents + obligations),
  `review_queue.md` (OK / WARN / REVIEW + tickets), `reminders_staged.json`
- sorted documents in `30_Anonymized/<JURIS>/` (`_Unsorted/` when the name is bad)
- staged Apple Reminders (never auto-created)
- draft reports in `50_Reports/`

Nothing is ever filed, paid, or remitted. The accountant signs off.

## Layout

```
accounting-skill/
├── agents/
│   └── corp-accountant.md              # pi subagent
├── skills/
│   └── corp-accounting/
│       ├── SKILL.md
│       ├── references/                 # data-pipeline, doc-types, review-queue,
│       │                               #   reminders-contract, report-contracts
│       └── scripts/
│           ├── scan_inbox.py           # hash, dedup, validate names
│           ├── extract_text.py         # local page-tagged text (PyMuPDF)
│           ├── ledger.py               # upsert extracted docs; render review queue
│           ├── file_docs.py            # sort processed docs into jurisdiction folders
│           ├── reminders.py            # stage proposals; mark created
│           ├── selftest.py             # end-to-end test on a throwaway vault
│           └── vaultlib.py             # shared helpers
├── install.sh
├── INSTALL.md
└── README.md
```

## Quick start

```bash
./install.sh          # wires the skill into ~/.pi/agent/settings.json + registers the agent
```

Restart pi, then in the Finance vault ask: *"process my corp accounting inbox"*, or run
`/skill:corp-accounting`.

Verify the pipeline without touching the vault:

```bash
python3 skills/corp-accounting/scripts/selftest.py
```

## Requirements

- Python 3.10+
- PyMuPDF (`pip install pymupdf`) — only for `extract_text.py`
- The `obsidian` CLI and a running Obsidian (report/note writes)
- The `apple-reminders` skill (reminder creation, after confirmation)

## Provenance

The pipeline is anchored on the vault's `AGENTS.md`: cloud AI reads only `30_Anonymized/`;
every fact carries provenance; no tax rate or deadline from memory; never auto-act on a
REVIEW item; never file or pay.
