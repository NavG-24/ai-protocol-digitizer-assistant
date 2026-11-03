"""Sprint 5b: compare the extraction with the study's ClinicalTrials.gov record.

The registry record is public structured data (API v2, no key needed). A mismatch is
not automatically an extraction error: the registry is often updated after the protocol
(for example actual vs planned enrollment, or a later amendment), so each row says which
side to check."""

import json
import re
import urllib.request

import answer_key as AK
import design as D

API = "https://clinicaltrials.gov/api/v2/studies/{}?format=json"

MASKING = {"NONE": "open label", "SINGLE": "single blind", "DOUBLE": "double blind",
           "TRIPLE": "double blind", "QUADRUPLE": "double blind"}
MODEL = {"PARALLEL": "parallel", "CROSSOVER": "crossover", "FACTORIAL": "factorial",
         "SINGLE_GROUP": "single group", "SEQUENTIAL": "other"}
PHASE = {"EARLY_PHASE1": "Phase 0", "PHASE1": "Phase 1", "PHASE2": "Phase 2", "PHASE3": "Phase 3",
         "PHASE4": "Phase 4", "NA": "not stated"}


def fetch(nct):
    """The study record as a dict (needs internet access to clinicaltrials.gov)."""
    with urllib.request.urlopen(API.format(nct), timeout=30) as r:
        return json.load(r)


def _criteria_counts(text):
    """Count bullet or numbered lines under the Inclusion and Exclusion headings."""
    counts, section = {"inclusion": 0, "exclusion": 0}, None
    for line in (text or "").splitlines():
        low = line.strip().lower()
        if low.startswith("inclusion"):
            section = "inclusion"
        elif low.startswith("exclusion"):
            section = "exclusion"
        elif section and re.match(r"^\s?(\*|-|•|\d+\.)\s+\S", line):
            counts[section] += 1  # top-level bullets only; sub-bullets are indented more
    return counts


def registry_values(rec):
    """The registry's values for the facts we extract."""
    p = rec.get("protocolSection", {})
    ident, des = p.get("identificationModule", {}), p.get("designModule", {})
    info, arms = des.get("designInfo", {}), p.get("armsInterventionsModule", {})
    elig = p.get("eligibilityModule", {})
    phases = des.get("phases", [])
    phase = ("Phase " + "/".join(PHASE.get(x, x).split()[-1] for x in phases)) if phases and phases != ["NA"] else "not stated"
    counts = _criteria_counts(elig.get("eligibilityCriteria"))
    enroll = des.get("enrollmentInfo", {})
    return {
        "nct_number": ident.get("nctId", ""),
        "full_title": ident.get("officialTitle", ""),
        "short_title": " / ".join(x for x in (ident.get("acronym"), ident.get("briefTitle")) if x),
        "sponsor_protocol_number": ident.get("orgStudyIdInfo", {}).get("id", ""),
        "sponsor_name": p.get("sponsorCollaboratorsModule", {}).get("leadSponsor", {}).get("name", ""),
        "study_phase": phase,
        "indication": "; ".join(p.get("conditionsModule", {}).get("conditions", [])),
        "investigational_products": "; ".join(i["name"] for i in arms.get("interventions", [])
                                              if "placebo" not in i.get("name", "").lower()),
        "n_primary_endpoints": str(len(p.get("outcomesModule", {}).get("primaryOutcomes", []))),
        "n_inclusion": str(counts["inclusion"]), "n_exclusion": str(counts["exclusion"]),
        "n_arms": str(len(arms.get("armGroups", []))),
        "design_model": MODEL.get(info.get("interventionModel"), "other"),
        "design_blinding": MASKING.get(info.get("maskingInfo", {}).get("masking"), "other"),
        "design_randomized": "yes" if info.get("allocation") == "RANDOMIZED" else "no",
        "design_enrollment": str(enroll.get("count", "")),
        "_enrollment_type": enroll.get("type", ""),
        "_arms": [a.get("label", "") for a in arms.get("armGroups", [])],
    }


HINT = {  # what usually explains a mismatch
    "design_enrollment": "Registry may show actual enrollment after the study closed.",
    "n_inclusion": "Registry criteria are often abridged or regrouped.",
    "n_exclusion": "Registry criteria are often abridged or regrouped.",
    "n_primary_endpoints": "Registry may split or merge primary outcomes.",
    "investigational_products": "Registry lists all interventions, sometimes with codes instead of names.",
    "design_blinding": "Registry 'triple/quadruple' masking is shown here as double blind.",
}


def compare(fields, design_data, rec):
    """One row per fact: protocol value, registry value, match, and what to check."""
    reg = registry_values(rec)
    ours = {k: v["value"] for k, v in (fields or {}).items()}
    if fields.get("short_title", {}).get("acronym"):
        ours["short_title"] = f"{fields['short_title']['acronym']} / {ours['short_title']}"
    ours.update({k: v["value"] for k, v in D.summary_values(design_data).items()})
    rows = []
    for key, label in [(k, l) for k, l, _, _ in AK.F.FIELDS] + D.SUMMARY:
        if key in ("protocol_version", "protocol_date", "other_ids", "primary_objective", "n_epochs"):
            continue  # not on the registry record in a comparable form
        if key not in reg:
            continue
        mine, theirs = ours.get(key), reg[key]
        if mine is None:
            status = "Not extracted"
        elif not str(theirs).strip():
            status = "Not on registry"
        else:
            ok, _ = AK.matches(key, theirs, mine)
            if not ok and key in ("full_title", "short_title", "sponsor_name", "indication"):
                ok = AK.similarity(mine, theirs) >= 0.6 or AK._alnum(mine.split("/")[0]) in AK._alnum(theirs)
            status = "Match" if ok else "Differs"
        note = HINT.get(key, "") if status == "Differs" else ""
        if key == "design_enrollment" and reg["_enrollment_type"]:
            theirs = f"{theirs} ({reg['_enrollment_type'].lower()})"
        rows.append({"Fact": label, "Protocol (extracted)": mine if mine is not None else "",
                     "ClinicalTrials.gov": theirs, "Result": status, "What to check": note})
    return rows
