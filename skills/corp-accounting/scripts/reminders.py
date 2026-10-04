#!/usr/bin/env python3
"""reminders.py — turn dated obligations into Apple Reminders.

Subcommands:
    stage [--vault P] [--json]        compute proposals from the ledger (writes state)
    push  [--vault P] [--list NAME] [--include-review] [--dry-run] [--json]
                                      create Apple Reminders for the proposals
    mark  --key KEY [--note TEXT]     record a reminder as created

Proposals come from the ledger obligations whose kind is a filing/payment/instalment/
response and whose date is in the future. `push` writes them to the Apple Reminders list
for the area (corp -> "Accounting - Creatix", personal -> "Accounting - Personal"),
with the source provenance and hashtags in the notes (Apple turns #tags into real tags),
and records each key in 40_Ledger/reminders_staged.json so re-runs never duplicate.

REVIEW-state obligations are NOT auto-pushed — they stay tickets (AGENTS.md rule 7);
pass --include-review to override. Set REMINDERS_CLI to point at the reminders binary.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402

REMINDER_KINDS = {"filing", "payment", "instalment", "response"}
DEFAULT_LIST_CORP = "Accounting - Creatix"
DEFAULT_LIST_PERSONAL = "Accounting - Personal"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _state_path(vault: str) -> str:
    return os.path.join(vault, V.LEDGER_REL, V.REMINDERS_FILE)


def _load(vault: str) -> dict:
    return V.load_json(_state_path(vault), None) or {"generated_at": None, "sent": {}, "proposals": []}


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
            "period": o.get("period"),
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


# --------------------------------------------------------------------------- push

def reminders_cli() -> str:
    return os.environ.get("REMINDERS_CLI") or os.path.expanduser(
        "~/.pi/agent/git/github.com/jfstgermain/apple-reminders-skill/skills/apple-reminders/bin/reminders")


def resolve_list(vault: str, override: str | None = None) -> str:
    if override:
        return override
    cfg = V.load_json(os.path.join(vault, V.LEDGER_REL, "reminders_config.json"), None) or {}
    if cfg.get("list"):
        return cfg["list"]
    base = os.path.basename(vault.rstrip("/")).lower()
    return DEFAULT_LIST_PERSONAL if "personal" in base else DEFAULT_LIST_CORP


def _tag(text: str | None) -> str:
    return "#" + re.sub(r"[^0-9A-Za-z]", "", text or "")


JURIS_TAG = {"RevenuQC": "#RevenuQuebec", "CRA": "#ARC", "Payroll": "#Paie", "Accountant": "#Comptable"}
JURIS_LABEL = {"RevenuQC": "Revenu Québec", "CRA": "ARC", "Payroll": "Paie", "Accountant": "Comptable"}
TYPE_TAG = {
    "RQ-CO17-ASSESSMENT": "#CO17", "RQ-DAS-ASSESSMENT": "#DAS",
    "RQ-TPS-TVH-ASSESSMENT": "#TPSTVQ", "RQ-COLLECTION": "#Recouvrement",
    "RQ-NON-PRODUCTION": "#NonProduction", "RQ-REFUND-HOLD": "#Remboursement",
    "RQ-STATEMENT": "#Releve", "RQ-PAYMENT": "#Paiement",
    "FINANCIAL-STATEMENT": "#EtatsFinanciers", "TAX-FILING": "#Production",
    "TAX-RETURN": "#Declaration", "EMAIL-THREAD": "#Correspondance",
    "RQ-CORRESPONDENCE": "#Correspondance",
}
KIND_TAG = {"payment": "#paiement", "filing": "#production",
            "instalment": "#acompte", "response": "#reponse"}


def build_tags(p: dict) -> str:
    tags = ["#accounting"]
    juris = p.get("jurisdiction") or ""
    tags.append(JURIS_TAG.get(juris) or _tag(juris))
    dtype = p.get("doc_type") or ""
    tags.append(TYPE_TAG.get(dtype) or _tag(dtype)[:24])
    kind = p.get("kind") or ""
    tags.append(KIND_TAG.get(kind) or _tag(kind))
    per = p.get("period") or ""
    if per:
        tags.append(_tag(per))
    out = []
    for t in tags:
        if len(t) > 1 and t not in out:
            out.append(t)
    return " ".join(out)


def build_notes(p: dict) -> str:
    src = p.get("source_file") or ""
    if p.get("page"):
        prov = f"{src} (p.{p['page']}) — « {p.get('snippet', '')} »"
    else:
        prov = src
    return f"{prov}\n{build_tags(p)}"


def build_title(p: dict) -> str:
    juris = p.get("jurisdiction") or ""
    head = " ".join(x for x in (JURIS_LABEL.get(juris, juris), p.get("doc_type")) if x)
    label = p.get("label") or p.get("kind") or "paiement"
    per = p.get("period")
    return f"{head} — {label}" + (f" ({per})" if per else "")


def push(vault: str, list_name: str | None = None, include_review: bool = False,
         dry_run: bool = False) -> dict:
    state = stage(vault)
    list_name = resolve_list(vault, list_name)
    cli = reminders_cli()
    sent = state.get("sent", {})
    pushed, skipped, failed = [], [], []
    for p in state["proposals"]:
        if p["key"] in sent:
            continue
        if p.get("state") == "REVIEW" and not include_review:
            skipped.append({"key": p["key"], "reason": "REVIEW — ticket, not auto-reminded"})
            continue
        cmd = [cli, "add", "--title", build_title(p), "--list", list_name,
               "--due", p["date"], "--notes", build_notes(p)]
        if dry_run:
            pushed.append({"key": p["key"], "title": build_title(p), "date": p["date"],
                           "list": list_name, "notes": build_notes(p)})
            continue
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as exc:  # noqa: BLE001
            failed.append({"key": p["key"], "error": str(exc)})
            continue
        if r.returncode != 0:
            failed.append({"key": p["key"], "error": (r.stderr or r.stdout).strip()[:200]})
            continue
        mark(vault, p["key"], note="apple-reminders")
        pushed.append({"key": p["key"], "title": build_title(p), "date": p["date"], "list": list_name})
    return {"vault": vault, "list": list_name, "cli": cli, "dry_run": dry_run,
            "pushed": pushed, "skipped": skipped, "failed": failed}


def main() -> int:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--vault", default=V.DEFAULT_VAULT)

    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_stage = sub.add_parser("stage", parents=[common])
    p_stage.add_argument("--json", action="store_true")

    p_push = sub.add_parser("push", parents=[common])
    p_push.add_argument("--list", help="target Apple Reminders list (default: by area)")
    p_push.add_argument("--include-review", action="store_true", help="also push REVIEW tickets")
    p_push.add_argument("--dry-run", action="store_true")
    p_push.add_argument("--json", action="store_true")

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

    if args.cmd == "push":
        res = push(vault, args.list, args.include_review, args.dry_run)
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            pre = "DRY-RUN " if res["dry_run"] else ""
            print(f"{pre}list: {res['list']}")
            for p in res["pushed"]:
                print(f"  + {p['date']}  {p.get('title', p['key'])}")
            for s in res["skipped"]:
                print(f"  ~ skipped {s['key'][:12]} ({s['reason']})")
            for f in res["failed"]:
                print(f"  ! failed {f['key'][:12]}: {f['error']}")
            print(f"pushed {len(res['pushed'])} · skipped {len(res['skipped'])} · failed {len(res['failed'])}")
        return 1 if res["failed"] else 0

    if args.cmd == "mark":
        mark(vault, args.key, args.note)
        print(f"marked sent: {args.key}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
