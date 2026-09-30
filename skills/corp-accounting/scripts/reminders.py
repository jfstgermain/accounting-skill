#!/usr/bin/env python3
"""reminders.py — stage Apple Reminder proposals from the ledger, and record
which ones a human actually created.

Reminders are NEVER written to Apple Reminders by this script. It only stages
proposals (deduplicated by source document + date + kind). The skill presents
them, the owner confirms, the agent creates them through the apple-reminders
skill, then calls ``mark``.

Subcommands:
    stage [--vault PATH] [--json]
    mark  --key KEY [--note TEXT] [--vault PATH]

State file: 40_Ledger/reminders_staged.json
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402

REMINDER_KINDS = {"filing", "payment", "instalment", "response"}


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _state_path(vault: str) -> str:
    return os.path.join(vault, V.LEDGER_REL, V.REMINDERS_FILE)


def _load(vault: str) -> dict:
    return V.load_json(_state_path(vault), None) or {
        "generated_at": None, "sent": {}, "proposals": []}


def stage(vault: str) -> dict:
    V.ensure_vault(vault)
    ledger = V.load_json(os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE), None) or {}
    state = _load(vault)
    sent = state.get("sent", {})

    proposals, seen = [], set()
    for o in ledger.get("obligations", []):
        key = o.get("id")
        if not key or key in sent or key in seen:
            continue
        if o.get("kind") not in REMINDER_KINDS or not o.get("date"):
            continue
        if o.get("state") == "REVIEW":
            continue  # REVIEW obligations are tickets, never reminders
        if o["date"] < dt.date.today().isoformat():
            continue  # past dates are surfaced as tickets, not future reminders
        seen.add(key)
        proposals.append({
            "key": key,
            "title": f"{o.get('jurisdiction', '?')} {o.get('doc_type', '?')}: {o.get('label') or o.get('kind')}",
            "date": o.get("date"),
            "kind": o.get("kind"),
            "jurisdiction": o.get("jurisdiction"),
            "doc_type": o.get("doc_type"),
            "source_file": o.get("source_file"),
            "page": o.get("page"),
            "snippet": o.get("snippet"),
            "state": o.get("state"),
        })

    state["proposals"] = proposals
    state["generated_at"] = _now()
    V.save_json(_state_path(vault), state)
    return state


def mark(vault: str, key: str, note: str | None = None) -> dict:
    state = _load(vault)
    state.setdefault("sent", {})[key] = {"sent_at": _now(), "note": note}
    state["proposals"] = [p for p in state.get("proposals", []) if p.get("key") != key]
    V.save_json(_state_path(vault), state)
    return state


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", default=V.DEFAULT_VAULT)

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_stage = sub.add_parser("stage", parents=[common])
    p_stage.add_argument("--json", action="store_true")

    p_mark = sub.add_parser("mark", parents=[common])
    p_mark.add_argument("--key", required=True)
    p_mark.add_argument("--note")

    args = ap.parse_args()
    vault = os.path.expanduser(args.vault)

    if args.cmd == "stage":
        state = stage(vault)
        if args.json:
            print(json.dumps(state, indent=2, ensure_ascii=False))
        else:
            props = state["proposals"]
            print(f"{len(props)} reminder proposal(s) staged (none created):")
            for p in props:
                print(f"  {p['date']}  [{p['state']}]  {p['title']}")
        return 0

    if args.cmd == "mark":
        mark(vault, args.key, args.note)
        print(f"marked sent: {args.key}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
