# Clinical Protocol Digitization & Study Assistant (prototype)

## What the prototype is

A Streamlit app that reads a public clinical trial protocol PDF and turns it into
structured, cited data. It extracts 12 study identification fields (Sprint 1 of the
stakeholder's plan) into a CDISC USDM v4.0 JSON file that is checked against CDISC's
schema, and extracts the Schedule of Activities into a visit-by-assessment grid. Every
value carries its PDF page; a person reviews values beside the highlighted page, and an
answer-key report measures accuracy. AI (Claude, through `llm.py`) reads free text and
tables; fixed-format identifiers (NCT, EudraCT / EU CT, IND) come from rules.

## How to run or use it

1. Python 3.11 or later. `py -m pip install -r requirements.txt`
2. Set your Anthropic API key: `set ANTHROPIC_API_KEY=...` (Windows). The key is never stored in the project.
   Or copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml` (git-ignored). If
   `APP_PASSWORD` is set, model calls need that password or the visitor's own key (see DEPLOY.md).
3. `py -m streamlit run app.py`, then open the page it prints.

Reviewers without an API key can still open protocols already extracted: results are saved
in `cache/`, keyed by a fingerprint of the PDF, so the same file never calls the model twice.

## What to try

1. Upload `protocols/NCT04736199.pdf` and click **Open upload**. On **Study fields → USDM v4.0**,
   click **Extract study fields** (a few cents). Pick a field to see its page with the source
   text highlighted; fix a value and save; download the USDM v4.0 JSON (shown as valid against
   the schema) and the provenance Excel.
2. On **Study design (Sprints 2-3)**, click **Extract study design** (about a minute,
   roughly $0.25). It lists objectives and endpoints, inclusion and exclusion criteria,
   interventions with dose and route, arms, epochs, and the design (model, blinding,
   randomization, planned enrollment), each cited and reviewable against its page. These are
   added to the USDM file on the first tab (arms, epochs, study cells, endpoints, eligibility).
3. On **Schedule of Activities**, click **Extract** (about a minute), then try Review & correct,
   Visit checklist, Visit calendar and Patient burden. Tick **Include the Schedule of
   Activities** on the first tab to add the visit timeline to the USDM file (Sprint 4).
4. On **Checks: rules & ClinicalTrials.gov** (Sprint 5, free), see 24 consistency rules run on
   the USDM file, then click **Fetch the registry record** to line the extraction up with the
   study's ClinicalTrials.gov record, with differences highlighted.
5. On **Accuracy vs answer key**, download the blank key, fill it in from the PDFs by hand, and
   upload it to get accuracy per protocol against the 90% target.
6. Ask a question at the bottom, for example "What is the primary objective?"; answers cite
   pages, or say "Not found in the extracted protocol data."

## Current limitations

- USDM output covers the 12 Sprint 1 fields, the Sprint 2-3 design (objectives, endpoints,
  eligibility, interventions, arms, epochs, study cells) and the SoA as a schedule timeline
  (one scheduled instance per visit, timings relative to the first Day 1 visit; a second
  Day 1 in a later part is timed from the first). Study cells are every arm crossed with every
  epoch; a few required USDM values are defaults, and the app lists them under the result.
- Validation is CDISC's USDM v4.0.0 JSON schema (`usdm_v4_api.json`, MIT, from
  github.com/cdisc-org/DDF-RA). The official CDISC CORE rules engine
  would not install, so `rules.py` runs 24 checks of the same kind; passing them is not a
  conformance claim.
- The ClinicalTrials.gov comparison needs internet access; registry criteria counts come from
  its bullet lists and may be abridged.
- Text-based PDFs only (no scanned protocols). Public protocols only.
- The Patient burden "needle / sample / dose" count is a keyword match.
