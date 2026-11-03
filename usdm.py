"""Assemble extracted fields into a CDISC USDM v4.0 JSON file and validate it.

Validation uses CDISC's own USDM v4.0.0 API definition (usdm_v4_api.json, from
github.com/cdisc-org/DDF-RA, MIT licence). The CDISC Python library (usdm4) validates
against the same definition but pulls in the full CDISC rules engine, which is too
heavy for this prototype. Codes are CDISC Controlled Terminology C-codes."""

import json
import re
from pathlib import Path

import jsonschema
from dateutil import parser as dateparser

import fields as F

SCHEMA_FILE = Path(__file__).parent / "usdm_v4_api.json"
CT = {"codeSystem": "http://www.cdisc.org", "codeSystemVersion": "2024-09-27"}

PHASES = [  # (patterns, C-code, decode)
    (["1/2", "I/II", "1-2"], "C15693", "Phase I/II Trial"),
    (["2/3", "II/III", "2-3"], "C15694", "Phase II/III Trial"),
    (["0"], "C54721", "Phase 0 Trial"),
    (["1", "I"], "C15600", "Phase I Trial"),
    (["2", "II"], "C15601", "Phase II Trial"),
    (["3", "III"], "C15602", "Phase III Trial"),
    (["4", "IV"], "C15603", "Phase IV Trial"),
]


class _Ids:
    def __init__(self):
        self.n = {}

    def __call__(self, kind):
        self.n[kind] = self.n.get(kind, 0) + 1
        return f"{kind}_{self.n[kind]}"


def phase_code(text):
    t = (text or "").upper().replace("PHASE", "").replace(" ", "")
    for patterns, code, decode in PHASES:
        if any(re.fullmatch(rf"{re.escape(p)}[AB]?", t) for p in patterns):
            return code, decode
    return None


def iso_date(text):
    try:
        return dateparser.parse(text, dayfirst=True, fuzzy=True).date().isoformat()
    except (ValueError, OverflowError, TypeError):
        return None


def build(fields, soa=None, name="Study", design=None):
    """USDM v4.0 Wrapper dict from the 12 fields, plus optionally the Sprint 2-3 design
    (objectives, endpoints, eligibility, interventions, arms, epochs) and the SoA grid's
    visits and assessments (as encounters and activities). Returns (doc, notes): notes list the
    values that are defaults rather than extracted, so nothing is passed off as found."""
    ids, notes = _Ids(), []

    def val(key):
        v = fields.get(key, {}).get("value", "")
        return "" if v == F.NOT_STATED else v.strip()

    def code(c, decode):
        return {"id": ids("Code"), "code": c, "decode": decode, "instanceType": "Code", **CT}

    orgs, idents = [], []

    def org(name_, type_code, type_decode, scheme, identifier):
        o = {"id": ids("Organization"), "name": name_, "type": code(type_code, type_decode),
             "identifierScheme": scheme, "identifier": identifier, "instanceType": "Organization"}
        orgs.append(o)
        return o

    sponsor = org(val("sponsor_name") or "Unknown sponsor", "C54149", "Pharmaceutical Company",
                  "Sponsor", val("sponsor_name") or "unknown")
    if val("sponsor_protocol_number"):
        idents.append({"id": ids("StudyIdentifier"), "text": val("sponsor_protocol_number"),
                       "scopeId": sponsor["id"], "instanceType": "StudyIdentifier"})
    if val("nct_number"):
        ctgov = org("ClinicalTrials.gov", "C93453", "Clinical Study Registry", "USGOV", "CT-GOV")
        idents.append({"id": ids("StudyIdentifier"), "text": val("nct_number"),
                       "scopeId": ctgov["id"], "instanceType": "StudyIdentifier"})
    for part in [p.strip() for p in val("other_ids").split(";") if ":" in p]:
        label, number = [x.strip() for x in part.split(":", 1)]
        if label.upper() == "IND":
            o = next((x for x in orgs if x["name"] == "FDA"), None) or org(
                "FDA", "C188863", "Regulatory Agency", "FDA", "FDA")
        else:
            o = next((x for x in orgs if x["name"] == "EMA"), None) or org(
                "EMA", "C188863", "Regulatory Agency", "EMA", "EMA")
        idents.append({"id": ids("StudyIdentifier"), "text": number, "scopeId": o["id"],
                       "instanceType": "StudyIdentifier"})

    titles = []
    for text, c, decode in [(val("full_title"), "C207616", "Official Study Title"),
                            (val("short_title"), "C207615", "Brief Study Title"),
                            ((fields.get("short_title") or {}).get("acronym", "").strip(), "C207646", "Study Acronym")]:
        if text:
            titles.append({"id": ids("StudyTitle"), "text": text, "type": code(c, decode),
                           "instanceType": "StudyTitle"})

    dates = []
    if val("protocol_date"):
        iso = iso_date(val("protocol_date"))
        if iso:
            dates.append({"id": ids("GovernanceDate"), "name": "Protocol approval date",
                          "type": code("C71476", "Approval Date"), "dateValue": iso,
                          "geographicScopes": [{"id": ids("GeographicScope"),
                                                "type": code("C68846", "Global"),
                                                "instanceType": "GeographicScope"}],
                          "instanceType": "GovernanceDate"})
        else:
            notes.append(f"Protocol date '{val('protocol_date')}' could not be read as a date.")

    interventions = [{"id": ids("StudyIntervention"), "name": p,
                      "role": code("C41161", "Protocol Agent"),
                      "type": code("C1909", "Pharmacologic Substance"),
                      "instanceType": "StudyIntervention"}
                     for p in [x.strip() for x in val("investigational_products").split(";") if x.strip()]]
    if interventions and not design:
        notes.append("Intervention type is set to Pharmacologic Substance by default.")

    phase = phase_code(val("study_phase"))
    design_obj = {
        "id": ids("InterventionalStudyDesign"), "name": "Study Design 1", "rationale": "",
        "studyType": code("C98388", "Interventional Study"),
        "studyPhase": {"id": ids("AliasCode"), "standardCode": code(*phase),
                       "instanceType": "AliasCode"} if phase else None,
        "arms": [], "studyCells": [], "epochs": [], "eligibilityCriteria": [],
        "population": {"id": ids("StudyDesignPopulation"), "name": "Study population",
                       "includesHealthySubjects": False, "instanceType": "StudyDesignPopulation"},
        "model": code("C82639", "Parallel Study"),
        "indications": [{"id": ids("Indication"), "name": val("indication"),
                         "isRareDisease": False, "instanceType": "Indication"}] if val("indication") else [],
        "objectives": [{"id": ids("Objective"), "name": "Primary objective",
                        "text": val("primary_objective"), "level": code("C85826", "Primary Objective"),
                        "instanceType": "Objective"}] if val("primary_objective") else [],
        "studyInterventionIds": [i["id"] for i in interventions],
        "instanceType": "InterventionalStudyDesign",
    }
    elig_items = []
    if design:
        _apply_design(design, design_obj, ids, code, interventions, elig_items, notes)
    else:
        notes.append("Not extracted yet, defaults for a valid file: study model (Parallel), "
                     "healthy volunteers (no), rare disease (no). Extract the study design "
                     "(Sprints 2-3) to fill arms, epochs, endpoints and eligibility.")
    if soa:  # Sprint 4: visits, assessments and the visit-by-assessment timeline
        _apply_soa(soa, design_obj, ids, code, notes)

    version = {"id": ids("StudyVersion"), "versionIdentifier": val("protocol_version") or "1",
               "rationale": "Extracted from the protocol PDF by the Protocol Assistant; reviewed by a person.",
               "studyIdentifiers": idents, "titles": titles, "organizations": orgs,
               "roles": [{"id": ids("StudyRole"), "name": "Sponsor", "code": code("C70793", "Clinical Study Sponsor"),
                          "organizationIds": [sponsor["id"]], "instanceType": "StudyRole"}],
               "dateValues": dates, "studyInterventions": interventions,
               "studyDesigns": [design_obj], "eligibilityCriterionItems": elig_items, "instanceType": "StudyVersion"}
    study = {"id": ids("Study"), "name": (fields.get("short_title") or {}).get("acronym") or name,
             "versions": [version], "instanceType": "Study"}
    doc = {"study": study, "usdmVersion": "4.0.0", "systemName": "Protocol Assistant",
           "systemVersion": "0.2"}
    return doc, notes


# CDISC codes for the Sprint 2-3 values
MODEL = {"parallel": ("C82639", "Parallel Study"), "crossover": ("C82637", "Crossover Study"),
         "factorial": ("C82638", "Factorial Study"), "single group": ("C82640", "Single Group Study")}
BLINDING = {"double blind": ("C15228", "Double Blind Study"), "single blind": ("C28233", "Single Blind Study"),
            "observer blind": ("C187674", "Observer Blind Study"), "open label": ("C49659", "Open Label Study")}
OBJ_LEVEL = {"primary": ("C85826", "Trial Primary Objective"), "secondary": ("C85827", "Trial Secondary Objective"),
             "exploratory": ("C163559", "Trial Exploratory Objective")}
END_LEVEL = {"primary": ("C94496", "Primary Endpoint"), "secondary": ("C139173", "Secondary Endpoint"),
             "exploratory": ("C170559", "Exploratory Endpoint")}
ROLE = {"investigational": ("C41161", "Protocol Agent"), "placebo": ("C753", "Placebo"),
        "active comparator": ("C68609", "Active Comparator"), "background": ("C165822", "Background Treatment"),
        "rescue": ("C165835", "Rescue Medications")}
ITYPE = {"drug": ("C1909", "Pharmacologic Substance"), "biological": ("C307", "Biological Agent"),
         "device": ("C16830", "Medical Device"), "procedure": ("C98769", "Physical Medical Procedure"),
         "behavioral": ("C15184", "Behavioral Intervention")}
ARM = {"investigational": ("C174266", "Investigational Arm"), "placebo control": ("C174268", "Placebo Control Arm"),
       "active comparator": ("C174267", "Active Comparator Arm"), "no intervention": ("C174270", "No Intervention Arm")}
EPOCH = {"screening": ("C202487", "Screening Epoch"), "run-in": ("C98779", "Run-in Period"),
         "treatment": ("C101526", "Treatment Epoch"), "blinded treatment": ("C102255", "Blinded Treatment Epoch"),
         "open label treatment": ("C102256", "Open Label Treatment Epoch"), "follow-up": ("C202578", "Follow-Up Epoch"),
         "long-term follow-up": ("C202577", "Long-term Follow-up Epoch"), "washout": ("C42872", "Washout Period")}


def _apply_design(d, des, ids, code, interventions, elig_items, notes):
    """Fill the study design object from the Sprint 2-3 extraction (in place)."""
    other = []
    info = d.get("design") or {}
    if info.get("model") in MODEL:
        des["model"] = code(*MODEL[info["model"]])
    else:
        other.append("study model")
    if info.get("blinding") in BLINDING:
        des["blindingSchema"] = {"id": ids("AliasCode"), "standardCode": code(*BLINDING[info["blinding"]]),
                                 "instanceType": "AliasCode"}
    des["population"]["includesHealthySubjects"] = bool(info.get("healthy_volunteers"))
    if info.get("planned_enrollment"):
        des["population"]["plannedEnrollmentNumber"] = {"id": ids("Quantity"), "value": info["planned_enrollment"],
                                                        "instanceType": "Quantity"}

    if d.get("interventions"):  # richer than the Sprint 1 product names, so they replace them
        interventions.clear()
        for it in d["interventions"]:
            desc = ", ".join(x for x in (it.get("dose"), it.get("route"), it.get("frequency")) if x)
            interventions.append({"id": ids("StudyIntervention"), "name": it["name"], "description": desc or None,
                                  "role": code(*ROLE.get(it["role"], ("C17649", "Other"))),
                                  "type": code(*ITYPE.get(it["type"], ("C17649", "Other"))),
                                  "instanceType": "StudyIntervention"})
        des["studyInterventionIds"] = [i["id"] for i in interventions]

    if d.get("objectives"):
        des["objectives"] = [{
            "id": ids("Objective"), "name": f"{o['level'].title()} objective {n}", "text": o["text"],
            "level": code(*OBJ_LEVEL[o["level"]]), "instanceType": "Objective",
            "endpoints": [{"id": ids("Endpoint"), "name": f"{o['level'].title()} endpoint {n}.{m}",
                           "text": e["text"], "purpose": "", "level": code(*END_LEVEL[o["level"]]),
                           "instanceType": "Endpoint"} for m, e in enumerate(o["endpoints"], 1)]}
            for n, o in enumerate(d["objectives"], 1)]

    crits = []
    for c in d.get("eligibility", []):
        item = {"id": ids("EligibilityCriterionItem"), "name": f"{c['category'][:3].upper()}{c['number']}",
                "text": c["text"], "instanceType": "EligibilityCriterionItem"}
        elig_items.append(item)
        cat = ("C25532", "Inclusion") if c["category"] == "inclusion" else ("C25370", "Exclusion")
        crits.append({"id": ids("EligibilityCriterion"), "name": item["name"], "category": code(*cat),
                      "identifier": str(c["number"]), "criterionItemId": item["id"],
                      "instanceType": "EligibilityCriterion"})
    for a, b in zip(crits, crits[1:]):
        a["nextId"], b["previousId"] = b["id"], a["id"]
    des["eligibilityCriteria"] = crits
    des["population"]["criterionIds"] = [c["id"] for c in crits]

    by_name = {i["name"].lower(): i["id"] for i in interventions}
    arms = [{"id": ids("StudyArm"), "name": a["name"], "description": a.get("description") or None,
             "type": code(*ARM.get(a["type"], ("C17649", "Other"))),
             "dataOriginDescription": "Data collected in this study",
             "dataOriginType": code("C188866", "Data Generated Within Study"),
             "instanceType": "StudyArm"} for a in d.get("arms", [])]
    epochs = [{"id": ids("StudyEpoch"), "name": e["name"], "description": e.get("description") or None,
               "type": code(*EPOCH.get(e["type"], ("C17649", "Other"))), "instanceType": "StudyEpoch"}
              for e in d.get("epochs", [])]
    for a, b in zip(epochs, epochs[1:]):
        a["nextId"], b["previousId"] = b["id"], a["id"]
    elements, cells = [], []
    for arm, src in zip(arms, d.get("arms", [])):
        given = [by_name[n.lower()] for n in src.get("interventions", []) if n.lower() in by_name]
        for ep, esrc in zip(epochs, d.get("epochs", [])):
            treating = "treatment" in esrc["type"]
            el = {"id": ids("StudyElement"), "name": f"{arm['name']} - {ep['name']}",
                  "studyInterventionIds": given if treating else [], "instanceType": "StudyElement"}
            elements.append(el)
            cells.append({"id": ids("StudyCell"), "armId": arm["id"], "epochId": ep["id"],
                          "elementIds": [el["id"]], "instanceType": "StudyCell"})
    des.update(arms=arms, epochs=epochs, elements=elements, studyCells=cells)
    if other:
        notes.append("Defaults used for: " + ", ".join(other) + ".")
    notes.append("Study cells are every arm crossed with every epoch; treatment epochs carry the "
                 "arm's interventions. Rare disease is set to no by default.")


# CDISC codes for USDM timings
TIMING_TYPE = {"fixed": ("C201358", "Fixed Reference"), "before": ("C201357", "Before"),
               "after": ("C201356", "After")}
START_TO_START = ("C201355", "Start to Start")


def _overlap(a, b):
    w = lambda t: set(re.findall(r"[a-z]+", (t or "").lower())) - {"period", "part", "epoch", "the", "of"}
    return len(w(a) & w(b))


def _apply_soa(soa, des, ids, code, notes):
    """Sprint 4: one encounter per visit, one activity per assessment, and a main schedule
    timeline with one scheduled instance per visit listing the activities done there.
    Timings are relative to the Day 1 visit, with the visit window when one is stated."""
    import views as V
    cols = V.visit_columns(soa)
    enc = [{"id": ids("Encounter"), "name": c["name"], "label": f"{c['visit']} ({c['period']})".strip(),
            "description": c.get("timing") or None, "type": code("C25716", "Visit"),
            "instanceType": "Encounter"} for c in cols]
    for a, b in zip(enc, enc[1:]):
        a["nextId"], b["previousId"] = b["id"], a["id"]
    acts = [{"id": ids("Activity"), "name": a["assessment"], "label": a.get("category") or None,
             "description": a.get("notes") or None, "instanceType": "Activity"} for a in soa["assessments"]]
    for a, b in zip(acts, acts[1:]):
        a["nextId"], b["previousId"] = b["id"], a["id"]
    at_visit = {c["index"]: [] for c in cols}
    for act, a in zip(acts, soa["assessments"]):
        for i in V.visits_for(a, cols):
            at_visit[i].append(act["id"])

    tl_id, exit_id = ids("ScheduleTimeline"), ids("ScheduleTimelineExit")
    epochs = des.get("epochs", [])
    inst = []
    for c, e in zip(cols, enc):
        si = {"id": ids("ScheduledActivityInstance"), "name": f"SAI {c['name']}", "label": c["name"],
              "encounterId": e["id"], "activityIds": at_visit[c["index"]],
              "instanceType": "ScheduledActivityInstance"}
        if epochs:
            best = max(epochs, key=lambda ep: _overlap(ep["name"], c["period"]))
            if _overlap(best["name"], c["period"]):
                si["epochId"] = best["id"]
        inst.append(si)
    if inst:
        inst[-1]["timelineExitId"] = exit_id

    # Timings: anchor on the first visit that is Day 1; others are before/after it.
    days = [V._day_numbers(c["timing"]) or V._day_numbers(c["visit"]) for c in cols]
    anchor = next((i for i, d in enumerate(days) if 1 in d), None)
    timings = []
    if anchor is not None:
        a_id = inst[anchor]["id"]
        for i, (c, d) in enumerate(zip(cols, days)):
            if not d:
                continue
            first = min(d)
            off = first - 1 if first > 0 else first  # no Day 0
            kind = "fixed" if i == anchor else ("before" if off < 0 else "after")
            t = {"id": ids("Timing"), "name": f"Timing {c['name']}", "type": code(*TIMING_TYPE[kind]),
                 "value": f"P{abs(off)}D", "valueLabel": c["timing"] or c["visit"],
                 "relativeToFrom": code(*START_TO_START), "relativeFromScheduledInstanceId": inst[i]["id"],
                 "relativeToScheduledInstanceId": a_id, "instanceType": "Timing"}
            w = V._window_days(c.get("window"))
            if w:
                t.update(windowLower=f"P{w}D", windowUpper=f"P{w}D", windowLabel=c["window"])
            timings.append(t)
            enc[i]["scheduledAtId"] = t["id"]
    des["encounters"], des["activities"] = enc, acts
    des["scheduleTimelines"] = [{
        "id": tl_id, "name": "Main timeline", "label": "Schedule of Activities",
        "description": "Visit-by-assessment schedule extracted from the protocol's SoA table",
        "mainTimeline": True, "entryCondition": "Participant has signed informed consent",
        "entryId": inst[0]["id"] if inst else "", "exits": [{"id": exit_id, "instanceType": "ScheduleTimelineExit"}],
        "timings": timings, "instances": inst, "instanceType": "ScheduleTimeline"}]
    dated = len(timings)
    notes.append(f"Schedule of Activities: {len(enc)} visits, {len(acts)} activities, "
                 f"{sum(len(v) for v in at_visit.values())} scheduled visit-activity pairs; "
                 f"{dated} of {len(enc)} visits have a timing relative to Day 1"
                 + ("" if anchor is not None else " (no Day 1 visit found, so timings are left out)") + ".")
    if epochs:
        missing = sum(1 for s in inst if "epochId" not in s)
        if missing:
            notes.append(f"{missing} visits could not be matched to an epoch by name.")


_validator = None


def validate(doc):
    """List of schema errors (empty when the file is valid USDM v4.0)."""
    global _validator
    if _validator is None:
        api = json.loads(SCHEMA_FILE.read_text(encoding="utf-8"))
        schema = {"$ref": "#/components/schemas/Wrapper-Input", "components": api["components"]}
        _validator = jsonschema.Draft202012Validator(schema)
    return [f"{'/'.join(map(str, e.absolute_path)) or '(root)'}: {e.message}"
            for e in sorted(_validator.iter_errors(doc), key=lambda e: list(map(str, e.absolute_path)))]


def provenance(fields):
    """Per-field audit trail to ship next to the USDM file."""
    return [{"field": F.LABELS[k], "usdm_target": t, "value": fields[k]["value"],
             "acronym": fields[k].get("acronym", ""), "pdf_page": fields[k]["page"],
             "source_text": fields[k]["source_text"], "confidence": fields[k]["confidence"],
             "method": fields[k].get("method", ""), "verified_on_page": fields[k].get("verified", False),
             "corrected_by_reviewer": fields[k].get("corrected", False)}
            for k, _, t, _ in F.FIELDS if k in fields]
