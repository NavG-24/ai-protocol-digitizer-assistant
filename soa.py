"""Core logic: get a protocol PDF, find its Schedule of Activities (SoA),
extract it to JSON with page citations, and answer questions from that JSON."""

import io
import json
import re
import urllib.request

from pypdf import PdfReader, PdfWriter

import llm

MAX_SOA_PAGES = 25
NOT_FOUND = "Not found in the extracted protocol data."


# ---------- Getting the PDF ----------

def fetch_from_registry(nct_or_url):
    """Download the protocol PDF for a ClinicalTrials.gov NCT ID or study link."""
    match = re.search(r"NCT\d{8}", nct_or_url.upper())
    if not match:
        raise ValueError("Enter an NCT ID like NCT06192108 or a ClinicalTrials.gov study link.")
    nct = match.group(0)
    api = f"https://clinicaltrials.gov/api/v2/studies/{nct}?fields=LargeDocumentModule"
    with urllib.request.urlopen(api, timeout=30) as r:
        study = json.load(r)
    docs = study.get("documentSection", {}).get("largeDocumentModule", {}).get("largeDocs", [])
    protocols = [d for d in docs if d.get("hasProtocol")]
    if not protocols:
        raise ValueError(f"{nct} has no protocol PDF posted on ClinicalTrials.gov.")
    url = f"https://cdn.clinicaltrials.gov/large-docs/{nct[-2:]}/{nct}/{protocols[0]['filename']}"
    with urllib.request.urlopen(url, timeout=120) as r:
        return nct, r.read()


# ---------- Step 1: find the SoA pages ----------

def locate_soa_pages(pdf_bytes):
    """Return (sorted 1-based PDF page numbers holding the SoA tables, cost)."""
    n_pages = len(PdfReader(io.BytesIO(pdf_bytes)).pages)
    schema = {"type": "object",
              "properties": {"pages": {"type": "array", "items": {"type": "integer"}}},
              "required": ["pages"], "additionalProperties": False}
    text, spent = llm.call_json("fast", [
        llm.pdf_block(pdf_bytes),
        {"type": "text", "text": (
            "This is a clinical trial protocol. List the PDF page numbers (1 = first page "
            "of the file, not the printed page number) that contain the Schedule of "
            "Activities table itself, including its continuation pages and footnotes. "
            "It may be called Schedule of Assessments, Schedule of Evaluations, Study "
            "Flow Chart or similar. Exclude the table of contents and pages that only "
            "mention the schedule in prose. Return an empty list if there is none.")},
    ], schema, max_tokens=4000)
    pages = sorted({p for p in json.loads(text)["pages"] if 1 <= p <= n_pages})
    return pages[:MAX_SOA_PAGES], spent


# ---------- Step 2: extract the SoA to JSON ----------

SOA_SCHEMA = {
    "type": "object",
    "properties": {
        "study": {"type": "string"},
        "visits": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "visit": {"type": "string"},
                "period": {"type": "string"},
                "timing": {"type": "string"},
                "window": {"type": "string"},
                "page": {"type": "integer"}},
            "required": ["visit", "period", "timing", "window", "page"],
            "additionalProperties": False}},
        "assessments": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "assessment": {"type": "string"},
                "category": {"type": "string"},
                "visits": {"type": "array", "items": {"type": "string"}},
                "notes": {"type": "string"},
                "page": {"type": "integer"}},
            "required": ["assessment", "category", "visits", "notes", "page"],
            "additionalProperties": False}},
    },
    "required": ["study", "visits", "assessments"],
    "additionalProperties": False,
}


def _single_page(pdf_bytes, page):
    reader, writer = PdfReader(io.BytesIO(pdf_bytes)), PdfWriter()
    writer.add_page(reader.pages[page - 1])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def extract_soa(pdf_bytes, pages):
    """Return (SoA dict, cost). Each SoA page is sent as its own titled document
    so the model can cite original PDF page numbers."""
    content = [llm.pdf_block(_single_page(pdf_bytes, p), title=f"PDF page {p}") for p in pages]
    content.append({"type": "text", "text": (
        "These pages hold a clinical trial protocol's Schedule of Activities. Convert it "
        "to JSON. List every visit column once in order, using the visit label exactly as "
        "written (for example 'Visit 3' or 'Week 12'). For each assessment row, list the "
        "visit labels where it is marked as done, and put conditions from footnotes in "
        "notes. 'page' is the number in the document title ('PDF page N') where the row or "
        "column appears. Copy what the table says; do not add assessments or visits that "
        "are not in it. Use an empty string when a field is not stated.")})
    text, spent = llm.call_json("strong", content, SOA_SCHEMA, max_tokens=64000, effort="medium")
    return json.loads(text), spent


# ---------- Step 3: answer questions from the JSON ----------

def answer(data, history, question):
    """Answer from the extracted data only (study fields and/or schedule).
    history is a list of {role, content} turns. Returns (answer text, cost)."""
    system = [
        {"type": "text", "text": (
            "You answer questions about one clinical trial protocol using ONLY the JSON "
            "below, which holds data extracted from it (study identification fields, study design "
            "and/or the Schedule of Activities). Cite the page for every fact as (p. N) using the "
            "JSON 'page' fields. If the JSON does not contain the answer, reply exactly: "
            f"\"{NOT_FOUND}\" and nothing else. Do not use outside knowledge or guess. "
            "Keep answers short.")},
        {"type": "text", "text": json.dumps(data, ensure_ascii=False),
         "cache_control": {"type": "ephemeral"}},
    ]
    return llm.call_text("strong", system, history + [{"role": "user", "content": question}])


def process(pdf_bytes):
    """Full pipeline. Returns (soa or None, pages, total cost)."""
    pages, c1 = locate_soa_pages(pdf_bytes)
    if not pages:
        return None, [], c1
    soa, c2 = extract_soa(pdf_bytes, pages)
    return soa, pages, c1 + c2
