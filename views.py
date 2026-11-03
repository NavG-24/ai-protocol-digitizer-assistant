"""Views built from the extracted Schedule of Activities JSON: the visit grid,
a per-visit checklist, a patient-burden summary and an Excel export.
Nothing here calls the API, so these views cost nothing to show."""

import io
import re

import pandas as pd

# Words that suggest a procedure involves a needle, a sample or a dose given in clinic.
# Keyword matching is a rough first pass, so the burden view says so on screen.
INVASIVE_WORDS = [
    "blood", "serum", "plasma", "venipuncture", "hematology", "haematology", "chemistry",
    "laboratory", "lab ", "labs", "sample", "pharmacokinetic", "pk ", "biopsy", "hba1c",
    "lipid", "glucose", "coagulation", "infusion", "injection", "biomarker", "genetic",
    "dna", "rna", "serology", "antibod", "lumbar", "bone marrow",
]


def _words(text):
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def visit_columns(soa):
    """One entry per visit column, with a unique display name.
    When two periods reuse a label (for example 'Day 1' in Part A and Part B),
    the period is added to the name so the columns stay separate."""
    visits = soa["visits"]
    counts = {}
    for v in visits:
        counts[v["visit"]] = counts.get(v["visit"], 0) + 1
    cols = []
    for i, v in enumerate(visits):
        name = v["visit"] if counts[v["visit"]] == 1 else f"{v['visit']} ({v['period']})"
        cols.append({"index": i, "name": name, **v})
    return cols


def visits_for(assessment, cols):
    """Indexes of the visit columns where this assessment is done.
    A label used by several periods is matched to the period that best fits
    the assessment's category (for example 'Part B Safety' goes to Part B)."""
    by_label = {}
    for c in cols:
        by_label.setdefault(c["visit"], []).append(c)
    category = _words(assessment.get("category", ""))
    found = []
    for label in assessment["visits"]:
        options = by_label.get(label, [])
        if len(options) > 1:
            scores = [len(category & _words(c["period"])) for c in options]
            best = max(scores)
            options = [c for c, s in zip(options, scores) if s == best]
        found += [c["index"] for c in options]
    return sorted(set(found))


def grid(soa):
    """The visit-by-assessment table as a DataFrame, 'X' where done."""
    cols = visit_columns(soa)
    rows = []
    for a in soa["assessments"]:
        done = set(visits_for(a, cols))
        row = {"Assessment": a["assessment"], "Category": a["category"], "Page": a["page"]}
        row.update({c["name"]: "X" if c["index"] in done else "" for c in cols})
        row["Notes"] = a["notes"]
        rows.append(row)
    return pd.DataFrame(rows)


def checklist(soa, visit_index):
    """Assessments done at one visit, in table order."""
    cols = visit_columns(soa)
    rows = []
    for a in soa["assessments"]:
        if visit_index in visits_for(a, cols):
            rows.append({"Category": a["category"], "Assessment": a["assessment"],
                         "Conditions / notes": a["notes"], "Page": a["page"]})
    return pd.DataFrame(rows, columns=["Category", "Assessment", "Conditions / notes", "Page"])


def checklist_markdown(study, visit, items):
    """A printable checklist with tick boxes."""
    title = f"Visit {visit['name']}" if visit["name"].isdigit() else visit["name"]
    lines = [f"# Visit checklist: {title}", "",
             f"Study: {study}", f"Period: {visit['period'] or '-'}",
             f"Timing: {visit['timing'] or '-'}   Window: {visit['window'] or '-'}", ""]
    category = None
    for _, r in items.iterrows():
        if r["Category"] != category:
            category = r["Category"]
            lines += ["", f"## {category or 'Other'}"]
        note = f" ({r['Conditions / notes']})" if r["Conditions / notes"] else ""
        lines.append(f"- [ ] {r['Assessment']}{note}  [p. {r['Page']}]")
    lines += ["", "Draft generated from the protocol's Schedule of Activities. "
              "Check against the protocol before use."]
    return "\n".join(lines)


def is_invasive(assessment):
    text = f" {assessment['assessment']} {assessment['category']} ".lower()
    return any(w in text for w in INVASIVE_WORDS)


def burden(soa):
    """Per-visit counts: all procedures, and those flagged as needle/sample/dose."""
    cols = visit_columns(soa)
    total = [0] * len(cols)
    invasive = [0] * len(cols)
    for a in soa["assessments"]:
        flag = is_invasive(a)
        for i in visits_for(a, cols):
            total[i] += 1
            invasive[i] += flag
    return pd.DataFrame([{"Visit": c["name"], "Period": c["period"], "Timing": c["timing"],
                          "Procedures": total[c["index"]],
                          "Needle / sample / dose": invasive[c["index"]]} for c in cols])


def excel_bytes(soa, name):
    """Workbook with the grid, the visit list and the burden summary."""
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        grid(soa).to_excel(xl, sheet_name="Schedule", index=False)
        pd.DataFrame(soa["visits"]).to_excel(xl, sheet_name="Visits", index=False)
        burden(soa).to_excel(xl, sheet_name="Patient burden", index=False)
        pd.DataFrame([{"Protocol": name, "Study": soa.get("study", ""),
                       "Note": "Draft extracted by AI from the protocol PDF. "
                               "Every row cites its PDF page; check before use."}]
                     ).to_excel(xl, sheet_name="About", index=False)
        for sheet in xl.book.worksheets:
            for col in sheet.columns:
                width = max(len(str(c.value or "")) for c in col)
                sheet.column_dimensions[col[0].column_letter].width = min(max(width + 2, 6), 60)
            sheet.freeze_panes = "B2" if sheet.title == "Schedule" else "A2"
    return out.getvalue()


# ---------- Visit calendar ----------

def _day_numbers(text):
    """Study days named in a timing like 'Day -28 to Day -5', 'Day 15' or 'Week 12'.
    Day 1 is the first dose; Week N is taken as Day 1 + 7N days. Returns [] when the
    timing is not a fixed day (for example 'Early termination')."""
    text = text or ""
    days = [int(d) for d in re.findall(r"\bday\s*(-?\d+)", text, re.I)]
    days += [1 + 7 * int(w) for w in re.findall(r"\bweek\s*(\d+)", text, re.I)]
    days += [1 + 30 * int(m) for m in re.findall(r"\bmonth\s*(\d+)", text, re.I)]
    return days


def _window_days(text):
    """'±3 days' or '+/- 2 days' -> 3 or 2; 0 when no window is stated."""
    m = re.search(r"(\d+)\s*d", text or "", re.I)
    return int(m.group(1)) if m else 0


def calendar(soa, first_dose):
    """Planned dates per visit from the first-dose date (Day 1)."""
    import datetime as dt
    rows = []
    for c in visit_columns(soa):
        days = _day_numbers(c["timing"]) or _day_numbers(c["visit"])
        window = _window_days(c["window"])
        if not days:
            rows.append({"Visit": c["name"], "Period": c["period"], "Timing": c["timing"],
                         "Earliest": None, "Target": None, "Latest": None,
                         "Note": "Not a fixed study day"})
            continue
        def date(d):  # study days skip day 0: Day -1 is the day before Day 1
            return first_dose + dt.timedelta(days=d - 1 if d > 0 else d)
        lo, hi = min(days), max(days)
        rows.append({"Visit": c["name"], "Period": c["period"], "Timing": c["timing"],
                     "Earliest": date(lo) - dt.timedelta(days=window),
                     "Target": date(lo) if lo == hi else None,
                     "Latest": date(hi) + dt.timedelta(days=window),
                     "Note": "Range" if lo != hi else (f"±{window} days" if window else "")})
    return pd.DataFrame(rows)


def ics_bytes(cal, study):
    """Calendar file with one all-day event per dated visit."""
    import datetime as dt
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Protocol SoA Assistant//EN"]
    for i, r in cal.iterrows():
        if r["Earliest"] is None or pd.isna(r["Earliest"]):
            continue
        start = r["Target"] if r["Target"] is not None and not pd.isna(r["Target"]) else r["Earliest"]
        end = (r["Latest"] if r["Target"] is None or pd.isna(r["Target"]) else start) + dt.timedelta(days=1)
        lines += ["BEGIN:VEVENT", f"UID:{study}-{i}@soa-assistant", f"DTSTAMP:{stamp}",
                  f"DTSTART;VALUE=DATE:{start:%Y%m%d}", f"DTEND;VALUE=DATE:{end:%Y%m%d}",
                  f"SUMMARY:{study} {r['Visit']}",
                  f"DESCRIPTION:Window {r['Earliest']:%Y-%m-%d} to {r['Latest']:%Y-%m-%d}",
                  "END:VEVENT"]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines).encode()


# ---------- Review and correct ----------

def render_page(pdf_bytes, page, scale=1.6):
    """PNG image of one PDF page (1-based)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        image = doc[page - 1].render(scale=scale).to_pil()
    finally:
        doc.close()
    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


def review_rows(soa, reviewed, page):
    """Editable rows for the assessments cited on one page."""
    rows = []
    for i, a in enumerate(soa["assessments"]):
        if a["page"] == page:
            rows.append({"row": i, "Checked": i in reviewed, "Assessment": a["assessment"],
                         "Visits": ", ".join(a["visits"]), "Notes": a["notes"]})
    return pd.DataFrame(rows, columns=["row", "Checked", "Assessment", "Visits", "Notes"])


def apply_review(soa, reviewed, edited):
    """Write edited rows back into the SoA. Returns (new soa, reviewed set,
    number of rows changed, visit labels that match no visit column)."""
    import copy
    soa = copy.deepcopy(soa)
    reviewed = set(reviewed)
    labels = {v["visit"] for v in soa["visits"]}
    changed, unknown = 0, set()
    for _, r in edited.iterrows():
        a = soa["assessments"][int(r["row"])]
        visits = [v.strip() for v in str(r["Visits"]).split(",") if v.strip()]
        unknown |= {v for v in visits if v not in labels}
        new = {"assessment": str(r["Assessment"]), "visits": visits, "notes": str(r["Notes"] or "")}
        if any(a[k] != new[k] for k in new):
            a.update(new)
            a["corrected"] = True
            changed += 1
        (reviewed.add if r["Checked"] else reviewed.discard)(int(r["row"]))
    return soa, reviewed, changed, sorted(unknown)


def df_excel(sheets):
    """Workbook bytes from {sheet name: DataFrame}."""
    out = io.BytesIO()
    with pd.ExcelWriter(out, engine="openpyxl") as xl:
        for name, df in sheets.items():
            df.to_excel(xl, sheet_name=name[:31], index=False)
        for sheet in xl.book.worksheets:
            for col in sheet.columns:
                sheet.column_dimensions[col[0].column_letter].width = min(
                    max(len(str(c.value or "")) for c in col) + 2, 70)
            sheet.freeze_panes = "A2"
    return out.getvalue()
