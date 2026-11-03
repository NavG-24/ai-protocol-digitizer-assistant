"""Answer key: an Excel template a person fills in from the protocol, and an
accuracy report comparing the extracted fields against it."""

import io
import re

import pandas as pd

import design as D
import fields as F
import usdm

COLUMNS = ["Protocol", "Field key", "Field", "Expected value", "Expected PDF page", "Notes"]
LABELS = {**F.LABELS, **dict(D.SUMMARY)}
TEXT_FIELDS = {"full_title", "short_title", "sponsor_name", "protocol_version", "indication",
               "primary_objective"}


def template_bytes(protocols):
    """Blank answer key with one row per protocol and field."""
    rows = [{"Protocol": p, "Field key": k, "Field": label, "Expected value": "",
             "Expected PDF page": "", "Notes": ""} for p in protocols
            for k, label in [(k, label) for k, label, _, _ in F.FIELDS] + D.SUMMARY]
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        pd.DataFrame(rows, columns=COLUMNS).to_excel(xl, sheet_name="Answer key", index=False)
        pd.DataFrame({"How to fill it in": [
            "Read each value from the protocol PDF yourself, without looking at the app's output.",
            f"If the protocol does not state a field, write: {F.NOT_STATED}",
            "Other IDs: write them as 'EudraCT: 2020-003093-48; IND: 114769'.",
            "Investigational products: separate several with '; '.",
            "Expected PDF page is optional; 1 is the first page of the PDF file.",
            "Study design rows (Sprints 2-3): counts as numbers; model is parallel / crossover / "
            "factorial / single group; blinding is double blind / single blind / open label; "
            "randomized is yes or no.",
        ]}).to_excel(xl, sheet_name="Instructions", index=False)
        for sheet in xl.book.worksheets:
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width = min(
                    max(len(str(c.value or "")) for c in col) + 2, 70)
    return out.getvalue()


def _words(text):
    return set(re.findall(r"[a-z0-9]+", str(text).lower()))


def _alnum(text):
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


def similarity(a, b):
    wa, wb = _words(a), _words(b)
    return len(wa & wb) / len(wa | wb) if wa | wb else 1.0


def matches(key, expected, got):
    """(correct?, similarity) for one field."""
    e, g = str(expected).strip(), str(got).strip()
    if _alnum(e) == _alnum(F.NOT_STATED) or _alnum(g) == _alnum(F.NOT_STATED):
        same = _alnum(e) == _alnum(g)
        return same, 1.0 if same else 0.0
    if key == "protocol_date":
        same = usdm.iso_date(e) is not None and usdm.iso_date(e) == usdm.iso_date(g)
        return same, 1.0 if same else 0.0
    if key == "study_phase":
        same = usdm.phase_code(e) is not None and usdm.phase_code(e) == usdm.phase_code(g)
        return same, 1.0 if same else 0.0
    if key == "other_ids":
        ne, ng = set(re.findall(r"\d[\d-]{4,}", e)), set(re.findall(r"\d[\d-]{4,}", g))
        return ne == ng, similarity(" ".join(ne), " ".join(ng))
    if key == "investigational_products":
        want = [x for x in (s.strip() for s in e.split(";")) if x]
        sim = sum(_alnum(w) in _alnum(g) for w in want) / len(want) if want else 0.0
        return sim == 1.0, sim
    if key in TEXT_FIELDS:
        sim = similarity(e, g)
        return sim >= 0.8 or (len(_alnum(e)) > 3 and (_alnum(e) in _alnum(g) or _alnum(g) in _alnum(e))), sim
    same = _alnum(e) == _alnum(g)
    return same, 1.0 if same else similarity(e, g)


def score(fields_by_protocol, key_df):
    """Compare extractions with the answer key. fields_by_protocol maps the protocol
    name (as written in the key's Protocol column) to its 12 fields.
    Returns (detail DataFrame, summary DataFrame)."""
    rows = []
    for _, r in key_df.iterrows():
        proto, key = str(r["Protocol"]).strip(), str(r["Field key"]).strip()
        expected = "" if pd.isna(r["Expected value"]) else str(r["Expected value"]).strip()
        if not expected or proto not in fields_by_protocol or key not in LABELS:
            continue
        got = fields_by_protocol[proto].get(key, {})
        correct, sim = matches(key, expected, got.get("value", ""))
        page = r.get("Expected PDF page")
        page_ok = None if pd.isna(page) or str(page).strip() == "" else int(float(page)) == got.get("page")
        rows.append({"Protocol": proto, "Field": LABELS[key], "Expected": expected,
                     "Extracted": got.get("value", ""), "Correct": correct,
                     "Similarity": round(sim, 2), "Page correct": page_ok,
                     "Confidence": got.get("confidence", ""), "Method": got.get("method", "")})
    detail = pd.DataFrame(rows)
    if detail.empty:
        return detail, detail
    summary = detail.groupby("Protocol").agg(Fields=("Correct", "size"), Correct=("Correct", "sum")).reset_index()
    total = pd.DataFrame([{"Protocol": "All", "Fields": summary["Fields"].sum(), "Correct": summary["Correct"].sum()}])
    summary = pd.concat([summary, total], ignore_index=True)
    summary["Accuracy"] = (summary["Correct"] / summary["Fields"] * 100).round(1).astype(str) + "%"
    summary["Meets 90% target"] = summary["Correct"] / summary["Fields"] >= 0.9
    return detail, summary
