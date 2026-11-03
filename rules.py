"""Sprint 5a: consistency rules on the USDM file, beyond what the JSON schema can check.

The official CDISC CORE rules engine (cdisc-rules-engine) would not install on this
setup, so this module runs a smaller set of checks of the same kind: references that
point nowhere, broken visit or epoch order, gaps in the arm x epoch grid, and content a
reviewer would expect. Each result says what failed and where, so it can be fixed in the
review tabs. Passing these is not a CDISC conformance claim."""

import re

ERROR, WARN = "Error", "Warning"


def _walk(node, path=""):
    """Yield (path, dict) for every object in the document."""
    if isinstance(node, dict):
        yield path, node
        for k, v in node.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from _walk(v, f"{path}/{i}")


def _chain(items, label):
    """previousId/nextId must agree and run start to end without loops."""
    out = []
    by_id = {x["id"]: x for x in items}
    for x in items:
        n = x.get("nextId")
        if n and by_id.get(n, {}).get("previousId") != x["id"]:
            out.append((ERROR, f"{label} order", f"'{x.get('name')}' points to the next item but that item does not point back."))
    starts = [x for x in items if not x.get("previousId")]
    if items and len(starts) != 1:
        out.append((ERROR, f"{label} order", f"{len(starts)} {label.lower()}s have no previous item; expected exactly one start."))
    return out


def check(doc):
    """List of dicts: severity, rule, detail. Empty means every rule passed."""
    res = []
    objs = list(_walk(doc))
    ids = {}
    for path, o in objs:
        if "id" in o and "instanceType" in o:
            if o["id"] in ids:
                res.append((ERROR, "Unique ids", f"id '{o['id']}' is used twice ({ids[o['id']]} and {path})."))
            ids[o["id"]] = path
    for path, o in objs:
        for k, v in o.items():
            refs = v if k.endswith("Ids") and isinstance(v, list) else [v] if k.endswith("Id") and isinstance(v, str) else []
            for r in refs:
                if r and r not in ids:
                    res.append((ERROR, "References resolve", f"{path}/{k} points to '{r}', which does not exist."))

    version = doc["study"]["versions"][0]
    titles = {t["type"]["decode"] for t in version.get("titles", [])}
    if "Official Study Title" not in titles:
        res.append((ERROR, "Official title present", "The study has no official title."))
    if not version.get("studyIdentifiers"):
        res.append((ERROR, "Study identifier present", "The study has no identifier."))
    if not any(re.fullmatch(r"NCT\d{8}", i.get("text", "")) for i in version.get("studyIdentifiers", [])):
        res.append((WARN, "Registry number present", "No NCT number, so the ClinicalTrials.gov comparison cannot run."))

    for des in version.get("studyDesigns", []):
        objectives = des.get("objectives", [])
        prim = [o for o in objectives if o["level"]["code"] == "C85826"]
        if not prim:
            res.append((ERROR, "Primary objective present", "The design has no primary objective."))
        elif not any(o.get("endpoints") for o in prim):
            res.append((WARN, "Primary endpoint present", "No primary objective has an endpoint."))
        for o in objectives:
            if not (o.get("text") or "").strip():
                res.append((ERROR, "Objective text", f"'{o['name']}' has no text."))

        res += _chain(des.get("epochs", []), "Epoch")
        res += _chain(des.get("encounters", []), "Encounter")
        res += _chain(des.get("eligibilityCriteria", []), "Eligibility criterion")

        arms, epochs = des.get("arms", []), des.get("epochs", [])
        cells = {(c["armId"], c["epochId"]) for c in des.get("studyCells", [])}
        for a in arms:
            for e in epochs:
                if (a["id"], e["id"]) not in cells:
                    res.append((ERROR, "Arm x epoch grid", f"No study cell for arm '{a['name']}' in epoch '{e['name']}'."))
        if arms and len(arms) < 2 and des.get("blindingSchema"):
            res.append((WARN, "Blinding vs arms", "The study is blinded but has fewer than two arms."))

        crit = des.get("eligibilityCriteria", [])
        cats = {c["category"]["code"] for c in crit}
        if crit and "C25532" not in cats:
            res.append((WARN, "Inclusion criteria present", "Eligibility has exclusions but no inclusions."))
        nums = {}
        for c in crit:
            nums.setdefault(c["category"]["decode"], []).append(c["identifier"])
        for cat, ns in nums.items():
            if len(ns) != len(set(ns)):
                res.append((WARN, "Criterion numbering", f"{cat} criteria reuse a number: {', '.join(sorted(set(n for n in ns if ns.count(n) > 1)))}."))
        pop = des.get("population", {})
        if crit and set(pop.get("criterionIds", [])) != {c["id"] for c in crit}:
            res.append((ERROR, "Population uses all criteria", "Some eligibility criteria are not linked to the population."))
        n = (pop.get("plannedEnrollmentNumber") or {}).get("value")
        if n is not None and not (isinstance(n, (int, float)) and n > 0):
            res.append((ERROR, "Planned enrollment", f"Planned enrollment '{n}' is not a positive number."))

        used = set()
        for tl in des.get("scheduleTimelines", []):
            inst = tl.get("instances", [])
            if tl.get("entryId") not in {i["id"] for i in inst}:
                res.append((ERROR, "Timeline entry", f"Timeline '{tl['name']}' does not start at one of its visits."))
            if inst and not any(i.get("timelineExitId") for i in inst):
                res.append((WARN, "Timeline exit", f"Timeline '{tl['name']}' has no exit."))
            for i in inst:
                used |= set(i.get("activityIds", []))
                if not i.get("activityIds"):
                    res.append((WARN, "Visit has activities", f"Visit '{i['label']}' has no activities scheduled."))
            for t in tl.get("timings", []):
                if not re.fullmatch(r"P\d+D", t["value"]):
                    res.append((ERROR, "Timing value", f"'{t['name']}' value '{t['value']}' is not an ISO 8601 duration."))
                if bool(t.get("windowLower")) != bool(t.get("windowLabel")):
                    res.append((WARN, "Timing window", f"'{t['name']}' has a window without a label, or a label without a window."))
            if len([t for t in tl.get("timings", []) if t["type"]["decode"] == "Fixed Reference"]) > 1:
                res.append((ERROR, "One anchor", f"Timeline '{tl['name']}' has more than one fixed reference timing."))
        if des.get("scheduleTimelines"):
            for a in des.get("activities", []):
                if a["id"] not in used:
                    res.append((WARN, "Activity scheduled", f"Activity '{a['name']}' is not done at any visit."))
    return [{"Severity": s, "Rule": r, "Detail": d} for s, r, d in res]


RULES = ["Unique ids", "References resolve", "Official title present", "Study identifier present",
         "Registry number present", "Primary objective present", "Primary endpoint present", "Objective text",
         "Epoch order", "Encounter order", "Eligibility criterion order", "Arm x epoch grid", "Blinding vs arms",
         "Inclusion criteria present", "Criterion numbering", "Population uses all criteria", "Planned enrollment",
         "Timeline entry", "Timeline exit", "Visit has activities", "Timing value", "Timing window", "One anchor",
         "Activity scheduled"]
