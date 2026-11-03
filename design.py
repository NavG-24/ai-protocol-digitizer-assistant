"""Sprints 2 and 3: objectives and endpoints, eligibility criteria, interventions,
arms, epochs and the overall study design, each item cited to a PDF page.

One model call reads the synopsis, the objectives and endpoints, the eligibility
criteria and the study design sections (found by headings). Every item is then
checked against its cited page, like the Sprint 1 fields."""

import json
import re

import fields as F
import llm
import parse

MAX_PAGES = 32

_CITE = {"page": {"type": "integer"}, "source_text": {"type": "string"}}
_REQ = ["page", "source_text"]


def _obj(props, required):
    return {"type": "object", "additionalProperties": False,
            "properties": {**props, **_CITE}, "required": required + _REQ}


def _enum(*values):
    return {"type": "string", "enum": list(values)}


SCHEMA = {"type": "object", "additionalProperties": False,
          "required": ["objectives", "eligibility", "interventions", "arms", "epochs", "design"],
          "properties": {
    "objectives": {"type": "array", "items": _obj({
        "level": _enum("primary", "secondary", "exploratory"), "text": {"type": "string"},
        "endpoints": {"type": "array", "items": _obj({"text": {"type": "string"}}, ["text"])}},
        ["level", "text", "endpoints"])},
    "eligibility": {"type": "array", "items": _obj({
        "category": _enum("inclusion", "exclusion"), "number": {"type": "string"},
        "text": {"type": "string"}}, ["category", "number", "text"])},
    "interventions": {"type": "array", "items": _obj({
        "name": {"type": "string"},
        "role": _enum("investigational", "placebo", "active comparator", "background", "rescue", "other"),
        "type": _enum("drug", "biological", "device", "procedure", "behavioral", "other"),
        "dose": {"type": "string"}, "route": {"type": "string"}, "frequency": {"type": "string"}},
        ["name", "role", "type", "dose", "route", "frequency"])},
    "arms": {"type": "array", "items": _obj({
        "name": {"type": "string"},
        "type": _enum("investigational", "placebo control", "active comparator", "no intervention", "other"),
        "description": {"type": "string"},
        "interventions": {"type": "array", "items": {"type": "string"}}},
        ["name", "type", "description", "interventions"])},
    "epochs": {"type": "array", "items": _obj({
        "name": {"type": "string"},
        "type": _enum("screening", "run-in", "treatment", "blinded treatment", "open label treatment",
                      "follow-up", "long-term follow-up", "washout", "other"),
        "description": {"type": "string"}}, ["name", "type", "description"])},
    "design": _obj({
        "model": _enum("parallel", "crossover", "factorial", "single group", "other"),
        "blinding": _enum("double blind", "single blind", "observer blind", "open label", "other"),
        "randomized": {"type": "boolean"}, "planned_enrollment": {"type": "integer"},
        "healthy_volunteers": {"type": "boolean"}},
        ["model", "blinding", "randomized", "planned_enrollment", "healthy_volunteers"]),
}}

SECTIONS = {"objectives": "Objectives and endpoints", "eligibility": "Eligibility criteria",
            "interventions": "Interventions", "arms": "Arms", "epochs": "Epochs (study periods)"}


def _is_toc(text):
    return text.count("....") > 8 or len(re.findall(r"\.{5,}\s*\d+", text)) > 5


def pages_to_read(texts):
    """Synopsis pages, plus the pages after headings for objectives, eligibility and
    design, skipping the table of contents. Returns sorted 1-based page numbers."""
    pages = set(range(1, min(F.FIRST_PAGES, len(texts)) + 1))
    headings = [(r"objectives\s+and\s+endpoints", 3), (r"inclusion\s+criteria", 7),
                (r"overall\s+design", 3), (r"study\s+intervention\(?s?\)?\s+administered", 2),
                (r"scientific\s+rationale\s+for\s+study\s+design", 1)]
    for pattern, span in headings:
        for i, t in enumerate(texts, start=1):
            if i > F.FIRST_PAGES and not _is_toc(t) and re.search(pattern, t, re.I):
                pages.update(range(i, min(i + span, len(texts) + 1)))
                break
    return sorted(pages)[:MAX_PAGES]


def extract(pdf_bytes):
    """Returns (design dict, cost). Items carry page, source_text and verified."""
    texts = parse.page_texts(pdf_bytes)
    pages = pages_to_read(texts)
    doc = "\n\n".join(f"=== PDF page {p} ===\n{texts[p - 1]}" for p in pages)
    prompt = (
        "Below is text from a clinical trial protocol, split by PDF page. Extract:\n"
        "- objectives: every objective with its level and its endpoints;\n"
        "- eligibility: every inclusion and exclusion criterion, numbered as in the protocol, "
        "with its full text (sub-points included);\n"
        "- interventions: each study intervention (investigational, placebo, comparators, "
        "background) with dose, route and frequency as written;\n"
        "- arms: each treatment arm with the interventions it receives (names as in "
        "'interventions');\n"
        "- epochs: the study periods in order (for example screening, treatment, follow-up);\n"
        "- design: model, blinding, whether randomized, planned number of participants "
        "(0 if not stated), and whether healthy volunteers are enrolled.\n"
        "For every item give the PDF page from the '=== PDF page N ===' marker and copy the "
        "exact line it came from as source_text. Use only this text; do not guess or add "
        "items that are not stated. Prefer the main body sections over the synopsis when "
        "both give the same item, and do not list an item twice.\n\n" + doc)
    text, spent = llm.call_json("strong", [{"type": "text", "text": prompt}], SCHEMA,
                                max_tokens=32000, effort="low")
    found = json.loads(text)
    for item in _items(found):
        item["verified"] = _check(item, texts)
    found["pages_read"] = pages
    return found, spent


def _check(item, texts):
    """True when the item's source text (or its text/name) is on the cited page. A long
    quote found on another page moves the citation there; a short name (like "Placebo")
    found elsewhere does not, since it appears on many pages, and is flagged instead."""
    probe = {"value": item.get("text") or item.get("name") or "", "source_text": item.get("source_text", ""),
             "page": item.get("page", 0)}
    ok = F.verify(probe, texts)
    longest = max(len(" ".join(str(x).split())) for x in (probe["value"], probe["source_text"]))
    if probe.get("page_corrected") and longest < 25:
        return False
    item["page"] = probe["page"]
    return ok


def _items(d):
    for key in ["objectives", "eligibility", "interventions", "arms", "epochs"]:
        for it in d.get(key, []):
            yield it
            for ep in it.get("endpoints", []) if key == "objectives" else []:
                yield ep
    if d.get("design"):
        yield d["design"]


# ---------- answer-key values ----------

SUMMARY = [  # key, label
    ("n_primary_endpoints", "Number of primary endpoints"),
    ("n_inclusion", "Number of inclusion criteria"),
    ("n_exclusion", "Number of exclusion criteria"),
    ("n_arms", "Number of arms"),
    ("n_epochs", "Number of epochs"),
    ("design_model", "Intervention model"),
    ("design_blinding", "Blinding"),
    ("design_randomized", "Randomized (yes/no)"),
    ("design_enrollment", "Planned enrollment"),
]


def summary_values(d):
    """Countable facts for the answer key, in the same shape as the 12 fields."""
    if not d:
        return {}
    des = d.get("design", {})
    v = {"n_primary_endpoints": sum(len(o["endpoints"]) for o in d["objectives"] if o["level"] == "primary"),
         "n_inclusion": sum(1 for c in d["eligibility"] if c["category"] == "inclusion"),
         "n_exclusion": sum(1 for c in d["eligibility"] if c["category"] == "exclusion"),
         "n_arms": len(d["arms"]), "n_epochs": len(d["epochs"]),
         "design_model": des.get("model", ""), "design_blinding": des.get("blinding", ""),
         "design_randomized": "yes" if des.get("randomized") else "no",
         "design_enrollment": des.get("planned_enrollment") or F.NOT_STATED}
    return {k: {"value": str(x), "page": des.get("page", 0) if k.startswith("design") else 0,
                "confidence": "", "method": "llm"} for k, x in v.items()}
