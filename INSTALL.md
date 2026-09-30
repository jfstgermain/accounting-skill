# Install

## 1. Run the installer

```bash
./install.sh
```

It is idempotent: it merges the `corp-accounting` skill path into
`~/.pi/agent/settings.json`, and copies `agents/corp-accountant.md` to
`~/.pi/agent/agents/`. Safe to re-run after edits.

## 2. Install PyMuPDF (for text extraction)

```bash
pip install pymupdf
```

Only `scripts/extract_text.py` needs it; the rest is pure standard library.

## 3. Restart pi

Skills and agents are discovered at startup.

## 4. Verify the pipeline

```bash
python3 skills/corp-accounting/scripts/selftest.py
```

Expected: `SELFTEST PASSED`. This runs against a temporary vault and never touches your real
documents.

## 5. Point it at the vault

Default vault path (baked into `vaultlib.DEFAULT_VAULT`):

```
~/Library/Mobile Documents/iCloud~md~obsidian/Documents/Finance/Corp Accounting
```

Override at runtime with `--vault PATH` on any script if your vault moves.

## 6. Usage

In pi, inside the Finance vault:

- *"process my corp accounting inbox"*
- or `/skill:corp-accounting`

The skill will scan `30_Anonymized/_Inbox/`, extract, update the ledger, sort documents,
render the review queue, stage reminders, and draft reports.

## Vault prerequisites

The vault should contain:

```
Corp Accounting/
├── 30_Anonymized/_Inbox/     # drop anonymized files here
├── 20_Extracted/
├── 40_Ledger/
├── 50_Reports/
└── 100_Docs/
```

The scripts create `30_Anonymized/<JURIS>/` and `_Unsorted/` as needed. Anonymization is a
manual step performed outside the vault.
