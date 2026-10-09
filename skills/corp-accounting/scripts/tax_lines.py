#!/usr/bin/env python3
"""tax_lines.py — extract income/return lines from tax documents.

Deterministic candidate extraction of key figures from:
  - Revenu Québec avis de cotisation (personal, 3-digit lines)
  - CRA T1 (personal, 5-digit lines) + embedded TP-1000.TE Québec summary
  - corporate FINANCIAL-STATEMENT (income statement + balance sheet, 2-year columns)
  - corporate TAX-FILING (T2 instalment base: taxable income, Part I tax, SBD, …)

Each fact carries page + verbatim snippet provenance; the model reviews before
trusting. Table cells are reconstructed from word positions (same y-baseline).

Subcommands:
    extract FILE [--json]            # print facts for one PDF
    enrich  --vault PATH [--json]    # merge facts into 20_Extracted/*.json + re-upsert
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import vaultlib as V  # noqa: E402

# --- RQ personal assessment (TPF-97 "Détail des calculs") --------------------
_RQ_LINE_RE = re.compile(r"^\s*(\d{3})\s+(\S.*)$")
_RQ_AMOUNT_RE = re.compile(r"\d[\d\s]*,\d{2}")

# --- CRA T1 ----------------------------------------------------------------
_T1_LABELS = {
    "10100": "Revenus d'emploi",
    "12000": "Dividendes de sociétés canadiennes imposables",
    "12010": "Dividendes autres que déterminés",
    "12100": "Intérêts et autres revenus de placement",
    "15000": "Revenu total",
}
_T1_MAIN_RE = re.compile(r"(\d{1,3}(?:\s\d{3})+\s\d{2})\s+(10100|12000|12010)\b")
_T1_TOTAL_RE = re.compile(r"(\d{1,3}(?:\s\d{3})+\s\d{2})\s+Revenu total\s+15000")

# --- TP-1000.TE (Québec transmission summary, embedded in the combined T1) ---
# Lines look like: "Revenu total (ligne 199) .... 160 141 00"
_TP1_LABELS = {
    "199": "Revenu total (TP-1)",
    "275": "Revenu net (TP-1)",
    "299": "Revenu imposable (TP-1)",
    "399": "Crédits d'impôt non remboursables (TP-1)",
    "479": "Solde à payer (TP-1)",
}

# --- Corporate FINANCIAL-STATEMENT (BILAN + RÉSULTATS, 2 columns) -------------
_FS_AMOUNT_RE = re.compile(r"(-?\(?\d[\d\s]*\)?)\s*\$")
_FS_LABELS = [
    ("HONORAIRES PROFESSIONNELS", "Honoraires professionnels"),
    ("REVENUS D'INTÉRÊTS", "Revenus d'intérêts"),
    ("GAINS (PERTES) EN CAPITAL", "Gains (pertes) en capital"),
    ("SALAIRES ET AVANTAGES SOCIAUX", "Salaires et avantages sociaux"),
    ("BÉNÉFICES AVANT IMPÔTS", "Bénéfices avant impôts"),
    ("IMPÔTS SUR LES BÉNÉFICES EXIGIBLES", "Impôts sur les bénéfices exigibles"),
    ("BÉNÉFICE NET", "Bénéfice net"),
    ("BÉNÉFICES NON RÉPARTIS", "Bénéfices non répartis"),
    ("PLACEMENTS", "Placements (société)"),
    ("CAPITAL-ACTIONS", "Capital-actions"),
    ("ENCAISSE", "Encaisse"),
    ("COMPTES À RECEVOIR", "Comptes à recevoir"),
    ("IMPÔTS SUR LES SOCIÉTÉS À PAYER", "Impôts sur les sociétés à payer"),
    ("RETENUES À LA SOURCE (DAS) À PAYER", "Retenues à la source (DAS) à payer"),
    ("TAXES (TPS & TVQ) À PAYER", "Taxes (TPS & TVQ) à payer"),
]

# --- Corporate TAX-FILING (T2 instalment base) --------------------------------
_T2_LABELS = [
    ("REVENU IMPOSABLE DU QUÉBEC", "Revenu imposable du Québec (CO-17)"),
    ("REVENU IMPOSABLE", "Revenu imposable (T2)"),
    ("IMPÔT FÉDÉRAL DE LA PARTIE I", "Impôt fédéral partie I (T2)"),
    ("IMPÔT REMBOURSABLE SUR LE REVENU DE PLACEMENTS", "Impôt remboursable RDTOH (T2)"),
    ("DÉDUCTION ACCORDÉE AUX PETITES ENTREPRISES", "Déduction petite entreprise (T2)"),
    ("ABATTEMENT D'IMPÔT FÉDÉRAL", "Abattement d'impôt fédéral (T2)"),
    ("IMPÔT DE LA PARTIE I À PAYER", "Impôt partie I à payer (T2)"),
]


def _rows(path: str) -> list[tuple[int, str]]:
    """Reconstruct each visual row as one left-to-right string, per page."""
    import pymupdf
    out = []
    with pymupdf.open(path) as doc:
        for page in doc:
            words = sorted(page.get_text("words"), key=lambda w: (round(w[1], 1), w[0]))
            if not words:
                continue
            rows, cur_y, cur = [], words[0][1], []
            for w in words:
                if abs(w[1] - cur_y) > 3.0:
                    rows.append(" ".join(cur))
                    cur_y, cur = w[1], []
                cur.append(w[4])
            if cur:
                rows.append(" ".join(cur))
            for r in rows:
                out.append((page.number + 1, r))
    return out


def _norm_amount(value: str) -> str:
    s = value.replace("\u00a0", " ").replace(" ", "").replace(",", ".")
    try:
        return f"{float(s):.2f}"
    except ValueError:
        return s


def _t1_value(amount: str) -> str:
    s = amount.replace(" ", "")
    if len(s) <= 2:
        return s
    return f"{s[:-2]}.{s[-2:]}"


def _period_year(period: str | None) -> str | None:
    m = re.search(r"20\d{2}", period or "")
    return m.group(0) if m else None


def _extract_rq(rows) -> list[dict]:
    facts = []
    for pno, raw in rows:
        m = _RQ_LINE_RE.match(raw)
        if not m:
            continue
        num, rest = m.group(1), m.group(2)
        amounts = list(_RQ_AMOUNT_RE.finditer(rest))
        if not amounts:
            continue
        label = rest[:amounts[0].start()].strip().rstrip("+-= ").strip()
        if not label:
            continue
        declared = _norm_amount(amounts[0].group(0))
        facts.append({"label": label, "value": declared,
                      "line": num, "page": pno, "snippet": raw[:200]})
        if len(amounts) > 1:
            etabli = _norm_amount(amounts[1].group(0))
            if etabli != declared:
                facts.append({"label": label + " (établi)", "value": etabli,
                              "line": num, "page": pno, "snippet": raw[:200]})
    return facts


def _extract_t1(rows) -> list[dict]:
    facts = []
    for pno, raw in rows:
        for m in _T1_MAIN_RE.finditer(raw):
            num = m.group(2)
            facts.append({"label": _T1_LABELS[num], "value": _t1_value(m.group(1)),
                          "line": num, "page": pno, "snippet": raw[:200]})
        m = _T1_TOTAL_RE.search(raw)
        if m:
            facts.append({"label": _T1_LABELS["15000"], "value": _t1_value(m.group(1)),
                          "line": "15000", "page": pno, "snippet": raw[:200]})
    return facts


def _fs_norm(token: str) -> str | None:
    neg = token.strip().startswith("(")
    s = token.replace("(", "").replace(")", "").replace("\u00a0", " ").replace(" ", "")
    s = s.replace(",", ".")
    try:
        v = float(s)
    except ValueError:
        return None
    return f"{-v:.2f}" if neg else f"{v:.2f}"


def _extract_tp1(rows) -> list[dict]:
    facts = []
    for pno, raw in rows:
        for m in re.finditer(r"\(ligne\s+(\d{3})\)", raw):
            num = m.group(1)
            canon = _TP1_LABELS.get(num)
            if canon is None:
                continue
            am = re.search(r"(\d{1,3}(?:\s\d{3})+\s\d{2})\b", raw[m.end():])
            if not am:
                continue
            facts.append({"label": canon, "value": _t1_value(am.group(1)),
                          "line": num, "page": pno, "snippet": raw[:200]})
    return facts


def _extract_fs(rows, period: str | None) -> list[dict]:
    cy = _period_year(period)
    py = str(int(cy) - 1) if cy else "précédent"
    facts = []
    for pno, raw in rows:
        matches = list(_FS_AMOUNT_RE.finditer(raw))
        if not matches:
            continue
        label = raw[:matches[0].start()].strip()
        if not label:
            continue
        key = re.sub(r"\s+", " ", label).upper()
        canon = next((c for k, c in _FS_LABELS if k == key), None)
        if canon is None:
            continue
        vals = [v for v in (_fs_norm(m.group(1)) for m in matches) if v is not None]
        if not vals:
            continue
        facts.append({"label": canon, "value": vals[0], "year": cy,
                      "page": pno, "snippet": raw[:200]})
        if len(vals) > 1:
            facts.append({"label": canon, "value": vals[1], "year": py,
                          "page": pno, "snippet": raw[:200]})
    return facts


def _extract_t2(rows, period: str | None) -> list[dict]:
    year = _period_year(period)
    # Only the "Calcul de la base des acomptes provisionnels" pages carry the
    # return's income/tax figures; the later schedules repeat "Revenu imposable"
    # with line numbers and derived values we must not pick up.
    calc_pages = {pno for pno, raw in rows
                  if "Calcul de la base des acomptes provisionnels" in raw}
    facts = []
    for pno, raw in rows:
        if pno not in calc_pages:
            continue
        # T2 instalment base is amount-first: "156 730 Revenu imposable",
        # optionally with "+" / "=" column markers between amount and label.
        m = re.match(r"^(\d[\d\s]*)\s+(.+)$", raw)
        if not m:
            continue
        amount, rest = m.group(1), m.group(2)
        rest = re.sub(r"^(?:[+=]\s*)+", "", rest).strip()
        rup = rest.upper()
        for key, canon in _T2_LABELS:
            if rup.startswith(key):
                facts.append({"label": canon, "value": _norm_amount(amount),
                              "year": year, "page": pno, "snippet": raw[:200]})
                break
    return facts


def _period_from_name(path: str) -> str | None:
    m = re.search(r"FY(\d{4})", os.path.basename(path).upper())
    if m:
        return f"FY{m.group(1)}"
    m = re.search(r"20\d{2}", os.path.basename(path))
    return m.group(0) if m else None


def extract(path: str, doc_type: str | None = None, period: str | None = None) -> list[dict]:
    """Return facts [{label,value,line?,year?,page,snippet}] for one PDF."""
    if not doc_type:
        base = os.path.basename(path).upper()
        doc_type = ("FINANCIAL-STATEMENT" if "FINANCIAL-STATEMENT" in base
                    else "TAX-FILING" if "TAX-FILING" in base
                    else "RQ-ASSESSMENT" if "ASSESSMENT" in base
                    else "TAX-RETURN" if "TAX-RETURN" in base else "")
    if not period:
        period = _period_from_name(path)
    rows = _rows(path)
    dt = (doc_type or "").upper()
    if dt == "FINANCIAL-STATEMENT":
        facts = _extract_fs(rows, period)
    elif dt == "TAX-FILING":
        facts = _extract_t2(rows, period)
    elif dt.startswith("RQ") or "ASSESSMENT" in dt:
        facts = _extract_rq(rows)
    else:  # TAX-RETURN / T1 + embedded TP-1000.TE
        facts = _extract_t1(rows) + _extract_tp1(rows)
    # dedupe on (label,value,year,line), keep first page
    seen, out = set(), []
    for f in sorted(facts, key=lambda f: (f.get("line") or "", f["page"])):
        k = (f["label"], f["value"], f.get("year"), f.get("line"))
        if k not in seen:
            seen.add(k)
            out.append(f)
    return out


def _merge_amounts(doc: dict, facts: list[dict]) -> int:
    existing = {(a.get("line"), a.get("label"), a.get("value"), a.get("year"))
                for a in doc.get("amounts", [])}
    added = 0
    for f in facts:
        key = (f.get("line"), f["label"], f["value"], f.get("year"))
        if key in existing:
            continue
        entry = {"label": f["label"], "value": f["value"], "page": f["page"],
                 "snippet": f["snippet"]}
        if f.get("line"):
            entry["line"] = f["line"]
        if f.get("year"):
            entry["year"] = f["year"]
        doc.setdefault("amounts", []).append(entry)
        existing.add(key)
        added += 1
    return added


def enrich(vault: str) -> dict:
    V.ensure_vault(vault)
    import ledger as L  # noqa: N813

    ledger_path = os.path.join(vault, V.LEDGER_REL, V.LEDGER_FILE)
    ledger = V.load_json(ledger_path, None) or {}
    docs = ledger.get("documents", {})
    targets = {k: v for k, v in docs.items()
               if v.get("doc_type") in ("RQ-ASSESSMENT", "TAX-RETURN",
                                        "FINANCIAL-STATEMENT", "TAX-FILING")}
    summary = []
    for doc_id, doc in targets.items():
        src = doc.get("source_file")
        if not src:
            summary.append({"doc_id": doc_id, "error": "no source_file"})
            continue
        pdf = os.path.join(vault, src)
        if not os.path.exists(pdf):
            summary.append({"doc_id": doc_id, "error": f"missing pdf {src}"})
            continue
        facts = extract(pdf, doc.get("doc_type"), doc.get("period"))
        if not facts:
            summary.append({"doc_id": doc_id, "facts": 0})
            continue
        jpath = os.path.join(vault, V.EXTRACTED_REL, f"{doc_id}.json")
        jdoc = V.load_json(jpath, None) or dict(doc)
        added = _merge_amounts(jdoc, facts)
        if added:
            marker = " income lines added (tax_lines.py)"
            notes = jdoc.get("notes") or ""
            jdoc["notes"] = notes if marker in notes else notes + marker
            V.save_json(jpath, jdoc)
            L.upsert(vault, jpath)
        summary.append({"doc_id": doc_id, "doc_type": doc.get("doc_type"),
                        "period": doc.get("period"), "facts": len(facts), "added": added})
    return {"vault": vault, "processed": summary}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ex = sub.add_parser("extract")
    p_ex.add_argument("file")
    p_ex.add_argument("--json", action="store_true")
    p_en = sub.add_parser("enrich")
    p_en.add_argument("--vault", default=V.DEFAULT_VAULT)
    p_en.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.cmd == "extract":
        facts = extract(os.path.expanduser(args.file))
        if args.json:
            print(json.dumps(facts, indent=2, ensure_ascii=False))
        else:
            for f in facts:
                tag = f"  (p{f['page']})"
                year = f"  [{f['year']}]" if f.get("year") else ""
                line = f"  line {f['line']}" if f.get("line") else ""
                print(f"  {f['label']:40} = {f['value']}{year}{line}{tag}")
            print(f"{len(facts)} fact(s)")
        return 0
    if args.cmd == "enrich":
        res = enrich(os.path.expanduser(args.vault))
        if args.json:
            print(json.dumps(res, indent=2, ensure_ascii=False))
        else:
            for r in res["processed"]:
                print(f"  {r.get('doc_type') or '-':20} {r.get('period') or '-':>8} "
                      f"facts={r.get('facts', 0)} added={r.get('added', 0)}"
                      + (f"  ERROR: {r['error']}" if r.get("error") else ""))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
