"""Sprint 1: extract 12 study identification fields, each with a citation.

Values with a fixed shape (NCT, EudraCT / EU CT and IND numbers) come from rules;
everything else comes from the language model, reading the text of the title page,
synopsis and objectives pages. Every value is then checked against the cited page:
if its source text is not on that page, confidence drops to low."""

import json
import re

import llm
import parse

# key, label, USDM v4.0 target (from the stakeholder's Sprint 1 plan), hint for the model
FIELDS = [
    ("full_title", "Full title", "StudyVersion.titles (Official)", "the full official protocol title"),
    ("short_title", "Short title / acronym", "StudyVersion.titles (Brief / Acronym)",
     "the short or brief title; put the study acronym (for example ARANOTE) in 'acronym'"),
    ("sponsor_protocol_number", "Sponsor protocol number", "StudyIdentifier",
     "the sponsor's protocol number or code, not the NCT or EudraCT number"),
    ("nct_number", "NCT number", "StudyIdentifier", "the ClinicalTrials.gov number, NCT + 8 digits"),
    ("other_ids", "Other IDs (EudraCT / EU CT, IND)", "StudyIdentifier",
     "other registry or regulatory numbers, formatted 'EudraCT: ...; IND: ...'"),
    ("sponsor_name", "Sponsor name", "Organization", "the sponsor company or institution"),
    ("protocol_version", "Protocol version / amendment", "StudyVersion.versionIdentifier",
     "the version and/or amendment of this document, for example 'Amendment 1, version 2.0'"),
    ("protocol_date", "Protocol date", "GovernanceDate",
     "the date of this protocol version or amendment, as written"),
    ("study_phase", "Study phase", "StudyDesign.studyPhase", "for example 'Phase 3' or 'Phase 1/2'"),
    ("indication", "Indication", "Indication", "the disease or condition studied"),
    ("investigational_products", "Investigational product name(s)", "StudyIntervention",
     "the investigational drug(s), separated by '; ' (do not list placebo or comparators)"),
    ("primary_objective", "Primary objective (text)", "Objective (Primary)",
     "the primary objective, quoted from the protocol"),
]
LABELS = {k: label for k, label, _, _ in FIELDS}
NOT_STATED = "not stated"
FIRST_PAGES = 15        # title page, history and synopsis are almost always here
EXTRA_PAGES = 4         # plus up to this many pages that state the objectives

_ITEM = {"type": "object", "additionalProperties": False,
         "required": ["value", "page", "source_text", "confidence"],
         "properties": {"value": {"type": "string"}, "page": {"type": "integer"},
                        "source_text": {"type": "string"},
                        "confidence": {"type": "string", "enum": ["high", "medium", "low"]}}}
_SHORT = json.loads(json.dumps(_ITEM))
_SHORT["properties"]["acronym"] = {"type": "string"}
_SHORT["required"] = _ITEM["required"] + ["acronym"]
SCHEMA = {"type": "object", "additionalProperties": False, "required": [k for k, *_ in FIELDS],
          "properties": {k: (_SHORT if k == "short_title" else _ITEM) for k, *_ in FIELDS}}


def _norm(text):
    return " ".join(str(text).lower().split())


def pages_to_read(texts):
    """1-based page numbers sent to the model: the first pages plus objectives pages."""
    pages = list(range(1, min(FIRST_PAGES, len(texts)) + 1))
    for i, t in enumerate(texts[FIRST_PAGES:], start=FIRST_PAGES + 1):
        if len(pages) >= FIRST_PAGES + EXTRA_PAGES:
            break
        if re.search(r"primary\s+objective", t, re.I) or re.search(r"objectives\s+and\s+endpoints", t, re.I):
            pages.append(i)
    return pages


# ---------- rules ----------

def _find(texts, pattern, flags=0):
    """First match of a regex in the document: (match, 1-based page) or (None, None)."""
    for i, t in enumerate(texts, start=1):
        m = re.search(pattern, t, flags)
        if m:
            return m, i
    return None, None


def rule_fields(texts):
    """Fields found by fixed patterns. Each value carries method='rule'."""
    out = {}
    m, page = _find(texts, r"\bNCT\d{8}\b")
    if m:
        out["nct_number"] = {"value": m.group(0), "page": page, "source_text": m.group(0),
                             "confidence": "high", "method": "rule"}
    parts, first_page, sources = [], None, []
    m, page = _find(texts, r"\b(?:EU\s*CT|EudraCT)(?:\s*(?:No\.?|Number|number))?\s*[:#]?\s*(20\d{2}-\d{6}-\d{2}(?:-\d{2})?)", re.I)
    if not m:
        m, page = _find(texts, r"\b(20\d{2}-\d{6}-\d{2}(?:-\d{2})?)\b")
    if m:
        number = m.group(1)
        parts.append(f"{'EU CT' if number.count('-') == 3 else 'EudraCT'}: {number}")
        first_page, sources = page, sources + [m.group(0)]
    m, page = _find(texts, r"\bIND(?:\s*(?:No\.?|Number|#))?\s*[:#]?\s*(\d{5,6})\b")
    if m:
        parts.append(f"IND: {m.group(1)}")
        first_page, sources = first_page or page, sources + [m.group(0)]
    if parts:
        out["other_ids"] = {"value": "; ".join(parts), "page": first_page,
                            "source_text": sources[0], "confidence": "high", "method": "rule"}
    return out


# ---------- the model ----------

def llm_fields(texts, pages):
    """Ask the model for all 12 fields from the given pages. Returns (dict, cost)."""
    doc = "\n\n".join(f"=== PDF page {p} ===\n{texts[p - 1]}" for p in pages)
    hints = "\n".join(f"- {k}: {hint}" for k, _, _, hint in FIELDS)
    prompt = (
        "Below is text from a clinical trial protocol, split by PDF page. Extract these "
        f"fields:\n{hints}\n\nFor each field give the value, the PDF page number from the "
        "'=== PDF page N ===' marker where it appears, the exact sentence or line it was "
        "taken from (copied word for word) as source_text, and your confidence. If the "
        f"protocol does not state a field, use value \"{NOT_STATED}\", page 0, empty "
        "source_text and confidence high. Use only the text below; do not guess.\n\n" + doc)
    text, spent = llm.call_json("strong", [{"type": "text", "text": prompt}], SCHEMA, max_tokens=6000)
    return json.loads(text), spent


# ---------- checking against the page ----------

def verify(field, texts):
    """True when the source text (or the value) is on the cited page. If it is on
    another page instead, the page is corrected."""
    if field["value"] == NOT_STATED:
        return True
    probes = [p for p in (field.get("source_text"), field.get("value")) if p and len(_norm(p)) >= 3]
    page = field.get("page") or 0
    for probe in probes:
        n = _norm(probe)[:120]
        if 1 <= page <= len(texts) and n in _norm(texts[page - 1]):
            return True
    for probe in probes:  # cited the wrong page?
        n = _norm(probe)[:120]
        for i, t in enumerate(texts, start=1):
            if n in _norm(t):
                field["page"], field["page_corrected"] = i, True
                return True
    return False


def extract(pdf_bytes):
    """All 12 fields: {key: {value, page, source_text, confidence, method, verified}}, cost."""
    texts = parse.page_texts(pdf_bytes)
    found, spent = llm_fields(texts, pages_to_read(texts))
    for key in found:
        found[key]["method"] = "llm"
    found.update(rule_fields(texts))  # rules win where they found something
    for key, f in found.items():
        f["verified"] = verify(f, texts)
        if not f["verified"]:
            f["confidence"] = "low"
    return {k: found[k] for k, *_ in FIELDS}, spent
