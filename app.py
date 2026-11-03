"""Streamlit app: load a clinical protocol, extract its study identification fields
into CDISC USDM v4.0 and its Schedule of Activities into a visit grid, review both
against the PDF, score them against an answer key, and ask questions with citations.
Run with:  py -m streamlit run app.py"""

import datetime as dt
import hashlib
import hmac
import json
import os
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

import answer_key
import ctgov
import design as D
import fields as F
import parse
import rules
import soa
import usdm
import views

CACHE = Path(__file__).parent / "cache"  # saved extractions, so a protocol is only paid for once

st.set_page_config(page_title="Protocol Assistant", layout="wide")
st.title("Clinical Protocol Digitization & Study Assistant")
st.caption("Use public protocols only: text and pages loaded here are sent to Anthropic's API "
           "(all model calls go through llm.py, so the model can be swapped).")

if "spent" not in st.session_state:
    st.session_state.spent = 0.0


def _setting(name):
    """A setting from Streamlit secrets (Streamlit Cloud) or, failing that, the environment."""
    try:
        if name in st.secrets:
            return st.secrets[name]
    except Exception:  # no secrets.toml when run locally
        pass
    return os.environ.get(name)


def access_panel():
    """Who pays for model calls. With APP_PASSWORD set (the hosted demo), the owner's key
    is used only after the password is entered; anyone can paste their own key instead.
    Without APP_PASSWORD (local run), the owner's key is used directly."""
    owner_key, password = _setting("ANTHROPIC_API_KEY"), _setting("APP_PASSWORD")
    if not password:
        st.session_state.api_key = owner_key
        return
    st.header("0. Access")
    entered = st.text_input("Demo password", type="password")
    own_key = st.text_input("...or your own Anthropic API key", type="password")
    if own_key:
        st.session_state.api_key = own_key.strip()
        st.caption("Using your key for this session only; it is not stored.")
    elif entered and hmac.compare_digest(entered, password):
        st.session_state.api_key = owner_key
        st.caption("Unlocked: new extractions and questions are enabled.")
    else:
        st.session_state.api_key = None
        if entered:
            st.error("Wrong password.")
        st.caption("Without a password or key you can open protocols already extracted "
                   "(for example NCT04736199, NCT06192108, NCT05882357) and run the free checks.")
    st.divider()


def _read(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _write(path, obj):
    CACHE.mkdir(exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")


def load(pdf_bytes, name):
    """Open a protocol and pick up anything already extracted for it. Nothing is sent
    to the model until an extract button is pressed."""
    key = hashlib.sha256(pdf_bytes).hexdigest()[:16]
    data = {"key": key, "name": name, "soa": None, "pages": [], "reviewed": [],
            "soa_done": False, "fields": None, "fields_reviewed": [], "design": None,
            "design_reviewed": []}
    saved = _read(CACHE / f"{key}.json")
    if saved:
        data.update(soa=saved["soa"], pages=saved["pages"], soa_done=True)
        data["name"] = saved.get("name", name)
    reviewed = _read(CACHE / f"{key}.reviewed.json")
    if reviewed:  # corrections saved in review mode replace the raw extraction
        data["soa"], data["reviewed"] = reviewed["soa"], reviewed["reviewed"]
    saved_fields = _read(CACHE / f"{key}.fields.json")
    if saved_fields:
        data["fields"], data["fields_reviewed"] = saved_fields["fields"], saved_fields["reviewed"]
    saved_design = _read(CACHE / f"{key}.design.json")
    if saved_design:
        data["design"], data["design_reviewed"] = saved_design["design"], saved_design["reviewed"]
    st.session_state.protocol = data
    st.session_state.pdf = pdf_bytes
    st.session_state.chat = []


def extract_soa(data):
    with st.spinner("Finding the Schedule of Activities and extracting it (about a minute)..."):
        table, pages, spent = soa.process(st.session_state.pdf)
    st.session_state.spent += spent
    data.update(soa=table, pages=pages, soa_done=True)
    _write(CACHE / f"{data['key']}.json", {"name": data["name"], "pages": pages, "soa": table})


def extract_fields(data):
    with st.spinner("Reading the title page, synopsis and objectives (about 20 seconds)..."):
        found, spent = F.extract(st.session_state.pdf)
    st.session_state.spent += spent
    data.update(fields=found, fields_reviewed=[])
    save_fields(data)


def extract_design(data):
    with st.spinner("Reading objectives, eligibility and study design sections (about a minute)..."):
        found, spent = D.extract(st.session_state.pdf)
    st.session_state.spent += spent
    data.update(design=found, design_reviewed=[])
    save_design(data)


def save_design(data):
    _write(CACHE / f"{data['key']}.design.json",
           {"name": data["name"], "design": data["design"], "reviewed": data["design_reviewed"]})


def save_fields(data):
    _write(CACHE / f"{data['key']}.fields.json",
           {"name": data["name"], "fields": data["fields"], "reviewed": data["fields_reviewed"]})


@st.cache_data(show_spinner=False)
def page_image(pdf_bytes, page, highlight=None):
    return parse.render(pdf_bytes, page, highlight)


def save_review(data):
    _write(CACHE / f"{data['key']}.reviewed.json", {"soa": data["soa"], "reviewed": data["reviewed"]})


def all_extracted_fields():
    """Fields of every protocol extracted so far, by protocol name (for the answer key)."""
    out = {}
    for f in sorted(CACHE.glob("*.fields.json")):
        saved = _read(f)
        out[saved["name"]] = dict(saved["fields"])
    for f in sorted(CACHE.glob("*.design.json")):
        saved = _read(f)
        out.setdefault(saved["name"], {}).update(D.summary_values(saved["design"]))
    return out


with st.sidebar:
    access_panel()
    st.header("1. Load a protocol")
    upload = st.file_uploader("Upload a protocol PDF", type="pdf")
    if upload and st.button("Open upload"):
        load(upload.getvalue(), Path(upload.name).stem)
    source = st.text_input("...or a ClinicalTrials.gov NCT ID or link", placeholder="NCT04736199")
    if st.button("Fetch from ClinicalTrials.gov") and source:
        try:
            nct, pdf = soa.fetch_from_registry(source)
            load(pdf, nct)
        except Exception as e:  # shown to the user rather than crashing the page
            st.error(str(e))
    st.divider()
    st.metric("API cost this session", f"${st.session_state.spent:.2f}")

data = st.session_state.get("protocol")
if not data:
    st.info("Load a protocol from the sidebar to begin.")
    st.stop()

st.subheader(data["name"])
main_fields, main_design, main_soa, main_checks, main_acc = st.tabs(
    ["Study fields → USDM v4.0", "Study design (Sprints 2-3)", "Schedule of Activities",
     "Checks: rules & ClinicalTrials.gov", "Accuracy vs answer key"])


# ---------------- Study fields and USDM ----------------
with main_fields:
    if not data["fields"]:
        st.write("Extract the 12 study identification fields (titles, identifiers, sponsor, "
                 "version, date, phase, indication, products, primary objective), each with "
                 "its page and the sentence it came from.")
        if st.button("Extract study fields (a few cents)", type="primary"):
            extract_fields(data)
            st.rerun()
    else:
        fl = data["fields"]
        found = sum(1 for f in fl.values() if f["value"] != F.NOT_STATED)
        verified = sum(1 for f in fl.values() if f.get("verified"))
        doc, notes = usdm.build(fl, data["soa"] if st.session_state.get("include_soa") else None,
                                name=data["name"], design=data["design"])
        errors = usdm.validate(doc)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fields found", f"{found} of {len(F.FIELDS)}")
        c2.metric("Source text found on cited page", f"{verified} of {len(F.FIELDS)}")
        c3.metric("Reviewed by a person", f"{len(data['fields_reviewed'])} of {len(F.FIELDS)}")
        c4.metric("USDM v4.0 schema", "Valid" if not errors else f"{len(errors)} errors")

        table_rows = [{"#": i + 1, "Field": label, "Value": fl[k]["value"] + (
                           f"  [acronym: {fl[k]['acronym']}]" if fl[k].get("acronym") else ""),
                       "Page": fl[k]["page"] or "", "Confidence": fl[k]["confidence"],
                       "Method": fl[k].get("method", ""), "On page": fl[k].get("verified", False),
                       "Reviewed": k in data["fields_reviewed"], "USDM target": target}
                      for i, (k, label, target, _) in enumerate(F.FIELDS)]
        st.dataframe(pd.DataFrame(table_rows), hide_index=True, width="stretch", column_config={
            "On page": st.column_config.CheckboxColumn(help="The quoted source text was found on the cited page"),
            "Reviewed": st.column_config.CheckboxColumn()})

        st.markdown("#### Review a field against the PDF")
        pick = st.selectbox("Field", [k for k, *_ in F.FIELDS], format_func=lambda k: F.LABELS[k])
        f = fl[pick]
        left, right = st.columns([1, 1])
        with left:
            if f["page"]:
                st.image(page_image(st.session_state.pdf, f["page"], f.get("source_text") or f["value"]),
                         width="stretch", caption=f"PDF page {f['page']}, source text highlighted")
            else:
                st.info("The model reported this field as not stated, so there is no page to show.")
        with right:
            st.caption(f"USDM target: {dict((k, t) for k, _, t, _ in F.FIELDS)[pick]}  |  "
                       f"method: {f.get('method', '')}  |  confidence: {f['confidence']}")
            st.text_area("Source text (as quoted)", f.get("source_text", ""), disabled=True, height=100)
            with st.form(f"review-{pick}"):
                value = st.text_area("Value", f["value"], height=100)
                acronym = st.text_input("Acronym", f.get("acronym", "")) if pick == "short_title" else None
                page = st.number_input("PDF page", min_value=0, value=int(f["page"] or 0))
                ok = st.checkbox("Checked against the PDF", value=pick in data["fields_reviewed"])
                if st.form_submit_button("Save field", type="primary"):
                    changed = value.strip() != f["value"] or int(page) != int(f["page"] or 0) or (
                        acronym is not None and acronym.strip() != f.get("acronym", ""))
                    if changed:
                        f.update(value=value.strip(), page=int(page), corrected=True,
                                 confidence="high", method=f.get("method", "") + "+reviewer")
                        if acronym is not None:
                            f["acronym"] = acronym.strip()
                    reviewed = set(data["fields_reviewed"])
                    (reviewed.add if ok else reviewed.discard)(pick)
                    data["fields_reviewed"] = [k for k, *_ in F.FIELDS if k in reviewed]
                    save_fields(data)
                    st.rerun()

        st.markdown("#### USDM v4.0 output")
        st.checkbox("Include the Schedule of Activities (visits, assessments and the visit timeline)",
                    key="include_soa", disabled=not data["soa"],
                    help="Extract the Schedule of Activities first to enable this.")
        st.caption("Study design (Sprints 2-3): " + ("included" if data["design"] else
                   "not extracted yet; extract it on the Study design tab to add endpoints, "
                   "eligibility, arms and epochs"))
        if errors:
            st.error("The USDM file does not pass schema validation yet:")
            st.code("\n".join(errors[:30]))
        else:
            st.success("Valid against the CDISC USDM v4.0.0 schema.")
        for n in notes:
            st.caption("Note: " + n)
        d1, d2, d3 = st.columns(3)
        d1.download_button("Download USDM v4.0 JSON", json.dumps(doc, indent=2, ensure_ascii=False),
                           file_name=f"{data['name']}-usdm-v4.json", mime="application/json")
        prov = pd.DataFrame(usdm.provenance(fl))
        d2.download_button("Download provenance (Excel)", views.df_excel({"Provenance": prov}),
                           file_name=f"{data['name']}-provenance.xlsx")
        if d3.button("Re-extract fields (costs a few cents)"):
            extract_fields(data)
            st.rerun()
        with st.expander("View the USDM JSON"):
            st.json(doc, expanded=False)


# ---------------- Sprint 5: rule checks and registry comparison ----------------
with main_checks:
    if not data["fields"]:
        st.info("Extract the study fields first (first tab). Both checks run on the extracted data "
                "and cost nothing.")
    else:
        doc5, _ = usdm.build(data["fields"], data["soa"] if st.session_state.get("include_soa") else None,
                             name=data["name"], design=data["design"])
        st.markdown("#### Rule checks on the USDM file")
        st.caption("Schema validation checks the file's shape. These rules check that it makes sense: "
                   "references point to real objects, visits and epochs are in order, every arm has a "
                   "cell in every epoch, the timeline starts and ends, and key content is present. "
                   "They are modelled on CDISC CORE rules but are not the official engine.")
        found = rules.check(doc5)
        failed = {r["Rule"] for r in found}
        c1, c2, c3 = st.columns(3)
        c1.metric("Rules passed", f"{len(rules.RULES) - len(failed)} of {len(rules.RULES)}")
        c2.metric("Errors", sum(r["Severity"] == "Error" for r in found))
        c3.metric("Warnings", sum(r["Severity"] == "Warning" for r in found))
        if found:
            st.dataframe(pd.DataFrame(found), hide_index=True, width="stretch")
        else:
            st.success("Every rule passed.")
        if not data["design"]:
            st.caption("Extract the study design to check objectives, arms, epochs and eligibility too.")
        if not st.session_state.get("include_soa"):
            st.caption("Tick 'Include the Schedule of Activities' on the first tab to check the timeline too.")

        st.markdown("#### Compare with ClinicalTrials.gov")
        nct = data["fields"].get("nct_number", {}).get("value", "")
        path = CACHE / f"{data['key']}.ctgov.json"
        saved = _read(path)
        if not nct.upper().startswith("NCT"):
            st.info("No NCT number was extracted, so there is no registry record to compare with.")
        else:
            st.caption(f"Reads the public registry record for {nct} (free, no API key) and lines it "
                       "up with what was extracted from the protocol. A difference is a prompt to "
                       "look, not proof of an error: the registry is often updated after the protocol.")
            if st.button("Fetch the registry record" if not saved else "Fetch again"):
                try:
                    _write(path, {"nct": nct, "fetched": dt.datetime.now().isoformat(timespec="minutes"),
                                  "record": ctgov.fetch(nct)})
                    st.rerun()
                except Exception as e:
                    st.error(f"Could not reach ClinicalTrials.gov: {e}")
            if saved:
                rows = pd.DataFrame(ctgov.compare(data["fields"], data["design"], saved["record"]))
                c1, c2, c3 = st.columns(3)
                c1.metric("Match", int((rows["Result"] == "Match").sum()))
                c2.metric("Differs", int((rows["Result"] == "Differs").sum()))
                c3.metric("Not compared", int(rows["Result"].isin(["Not extracted", "Not on registry"]).sum()))
                st.dataframe(rows.style.apply(lambda r: ["background-color: #fde2e1" if r["Result"] == "Differs"
                                                          else ""] * len(r), axis=1),
                             hide_index=True, width="stretch")
                st.caption(f"Registry record fetched {saved['fetched']}. Arms on the registry: "
                           + "; ".join(ctgov.registry_values(saved["record"])["_arms"]))
                st.download_button("Download comparison (Excel)", views.df_excel({"vs ClinicalTrials.gov": rows}),
                                   file_name=f"{data['name']}-vs-ctgov.xlsx")


# ---------------- Study design: Sprints 2 and 3 ----------------
def _ok(flag):
    return "yes" if flag else "check"


with main_design:
    if not data["design"]:
        st.write("Extract objectives and endpoints, inclusion and exclusion criteria, study "
                 "interventions, arms, epochs and the overall design (model, blinding, "
                 "randomization, planned enrollment). Every item is cited to a PDF page.")
        if st.button("Extract study design (about a minute, roughly $0.25)", type="primary"):
            extract_design(data)
            st.rerun()
    else:
        d = data["design"]
        items = list(D._items(d))
        n_end = sum(len(o["endpoints"]) for o in d["objectives"])
        n_inc = sum(1 for c in d["eligibility"] if c["category"] == "inclusion")
        c1, c2, c3, c4, c5 = st.columns(5)
        c1.metric("Objectives / endpoints", f"{len(d['objectives'])} / {n_end}")
        c2.metric("Inclusion / exclusion", f"{n_inc} / {len(d['eligibility']) - n_inc}")
        c3.metric("Arms", len(d["arms"]))
        c4.metric("Epochs", len(d["epochs"]))
        c5.metric("On cited page", f"{sum(1 for i in items if i.get('verified'))} of {len(items)}")
        info = d["design"]
        st.info(f"**Design:** {info['model']}, {info['blinding']}, "
                f"{'randomized' if info['randomized'] else 'not randomized'}, planned enrollment "
                f"{info['planned_enrollment'] or 'not stated'}, healthy volunteers "
                f"{'yes' if info['healthy_volunteers'] else 'no'} (p. {info['page']})")

        t_obj, t_elig, t_int, t_arm = st.tabs(
            ["Objectives & endpoints", "Eligibility", "Interventions", "Arms & epochs"])
        with t_obj:
            st.dataframe(pd.DataFrame([{"Level": o["level"], "Objective": o["text"],
                                        "Endpoints": "; ".join(e["text"] for e in o["endpoints"]),
                                        "Page": o["page"], "On page": _ok(o.get("verified"))}
                                       for o in d["objectives"]]), hide_index=True, width="stretch")
        with t_elig:
            st.dataframe(pd.DataFrame([{"Category": c["category"], "#": c["number"], "Criterion": c["text"],
                                        "Page": c["page"], "On page": _ok(c.get("verified"))}
                                       for c in d["eligibility"]]), hide_index=True, width="stretch")
        with t_int:
            st.dataframe(pd.DataFrame([{"Intervention": i["name"], "Role": i["role"], "Type": i["type"],
                                        "Dose": i["dose"], "Route": i["route"], "Frequency": i["frequency"],
                                        "Page": i["page"], "On page": _ok(i.get("verified"))}
                                       for i in d["interventions"]]), hide_index=True, width="stretch")
        with t_arm:
            a1, a2 = st.columns(2)
            a1.dataframe(pd.DataFrame([{"Arm": a["name"], "Type": a["type"],
                                        "Interventions": ", ".join(a["interventions"]), "Page": a["page"]}
                                       for a in d["arms"]]), hide_index=True, width="stretch")
            a2.dataframe(pd.DataFrame([{"#": n, "Epoch": e["name"], "Type": e["type"], "Page": e["page"]}
                                       for n, e in enumerate(d["epochs"], 1)]), hide_index=True, width="stretch")
            if d["arms"] and d["epochs"]:
                st.caption("Study cells: what each arm receives in each epoch")
                st.dataframe(pd.DataFrame(
                    [{"Arm": a["name"], **{e["name"]: (", ".join(a["interventions"]) if "treatment" in e["type"] else "-")
                                           for e in d["epochs"]}} for a in d["arms"]]),
                    hide_index=True, width="stretch")

        st.markdown("#### Review an item against the PDF")
        sec = st.selectbox("Section", list(D.SECTIONS), format_func=D.SECTIONS.get)
        if d[sec]:
            idx = st.selectbox("Item", range(len(d[sec])), format_func=lambda i: (
                f"{d[sec][i].get('number', '') and d[sec][i]['category'][:3].upper() + d[sec][i]['number'] + ': '}"
                + (d[sec][i].get("text") or d[sec][i].get("name", ""))[:90]))
            it, rid = d[sec][idx], f"{sec}:{idx}"
            left, right = st.columns([1, 1])
            with left:
                if it.get("page"):
                    st.image(page_image(st.session_state.pdf, it["page"], it.get("source_text") or it.get("text") or it.get("name")),
                             width="stretch", caption=f"PDF page {it['page']}, source text highlighted")
            with right:
                st.caption(f"On cited page: {_ok(it.get('verified'))}")
                st.text_area("Source text (as quoted)", it.get("source_text", ""), disabled=True, height=90)
                with st.form(f"design-{rid}"):
                    textkey = "text" if "text" in it else "name"
                    value = st.text_area(textkey.title(), it[textkey], height=140)
                    page = st.number_input("PDF page", min_value=0, value=int(it.get("page") or 0))
                    ok = st.checkbox("Checked against the PDF", value=rid in data["design_reviewed"])
                    if st.form_submit_button("Save item", type="primary"):
                        if value.strip() != it[textkey] or int(page) != int(it.get("page") or 0):
                            it.update({textkey: value.strip(), "page": int(page), "corrected": True})
                        reviewed = set(data["design_reviewed"])
                        (reviewed.add if ok else reviewed.discard)(rid)
                        data["design_reviewed"] = sorted(reviewed)
                        save_design(data)
                        st.rerun()
            st.caption(f"{len(data['design_reviewed'])} of {sum(len(d[k]) for k in D.SECTIONS)} items "
                       "checked by a person")
        else:
            st.write("Nothing was extracted for this section.")
        if st.button("Re-extract study design (about $0.25)"):
            extract_design(data)
            st.rerun()


# ---------------- Schedule of Activities ----------------
def soa_section(data):
    table = data["soa"]
    st.write(f"Schedule of Activities found on PDF pages {', '.join(map(str, data['pages']))}.")

    n_rows = len(table["assessments"])
    n_checked = len(data["reviewed"])
    n_fixed = sum(1 for a in table["assessments"] if a.get("corrected"))
    st.progress(n_checked / n_rows if n_rows else 0,
                text=f"Human review: {n_checked} of {n_rows} rows checked against the PDF, {n_fixed} corrected")

    tab_grid, tab_review, tab_check, tab_cal, tab_burden, tab_visits, tab_json = st.tabs(
        ["Assessment grid", "Review & correct", "Visit checklist", "Visit calendar", "Patient burden",
         "Visits", "Raw JSON"])
    with tab_review:
        page = st.selectbox("Schedule page", data["pages"], format_func=lambda p: f"PDF page {p}")
        left, right = st.columns([1, 1])
        with left:
            if st.session_state.get("pdf"):
                st.image(page_image(st.session_state.pdf, page), width="stretch")
            else:
                st.info("Load the protocol again to see its pages here.")
        with right:
            rows = views.review_rows(table, set(data["reviewed"]), page)
            st.caption(f"{len(rows)} rows cite this page. Tick each row once it matches the page, "
                       "or fix it. Visits are comma-separated visit labels.")
            edited = st.data_editor(rows, hide_index=True, width="stretch", key=f"edit-{page}",
                                    disabled=["row"], column_config={
                                        "row": None,
                                        "Checked": st.column_config.CheckboxColumn(width="small"),
                                        "Notes": st.column_config.TextColumn(width="large")})
            if st.button("Save this page", type="primary"):
                new, reviewed, changed, unknown = views.apply_review(table, data["reviewed"], edited)
                data["soa"], data["reviewed"] = new, sorted(reviewed)
                save_review(data)
                if unknown:
                    st.warning("These visit labels match no visit column: " + ", ".join(unknown))
                st.success(f"Saved. {changed} row(s) corrected on this page.")
                st.rerun()
            with st.expander("Valid visit labels"):
                st.write(", ".join(dict.fromkeys(v["visit"] for v in table["visits"])))
    with tab_cal:
        first = st.date_input("First dose date (Day 1)", dt.date.today() + dt.timedelta(days=30))
        cal = views.calendar(table, first)
        if cal["Earliest"].notna().any():
            shown = cal.copy()
            for col in ["Earliest", "Target", "Latest"]:
                shown[col] = shown[col].map(lambda d: d.strftime("%a %d %b %Y") if d is not None and not pd.isna(d) else "")
            st.dataframe(shown, hide_index=True, width="stretch")
            st.download_button("Add to calendar (.ics)", views.ics_bytes(cal, data["name"]),
                               file_name=f"{data['name']}-visits.ics", mime="text/calendar")
            st.caption("Week N is taken as Day 1 + 7N days. Visits tied to an event, such as early "
                       "termination, have no fixed date.")
        else:
            st.info("This protocol's schedule does not give visit days or weeks that could be read, "
                    "so no dates can be planned.")
    with tab_grid:
        st.dataframe(views.grid(table), hide_index=True, width="stretch")
        st.download_button("Download as Excel", views.excel_bytes(table, data["name"]),
                           file_name=f"{data['name']}-schedule.xlsx",
                           mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")
    with tab_check:
        cols = views.visit_columns(table)
        pick = st.selectbox("Visit", range(len(cols)), format_func=lambda i: (
            f"{cols[i]['name']}  ({cols[i]['period']})" if cols[i]["period"] and cols[i]["period"] not in cols[i]["name"]
            else cols[i]["name"]))
        visit = cols[pick]
        st.caption(f"Timing: {visit['timing'] or 'not stated'}  |  Window: {visit['window'] or 'not stated'}"
                   f"  |  Column on PDF page {visit['page']}")
        items = views.checklist(table, pick)
        st.write(f"**{len(items)} procedures** at this visit.")
        st.dataframe(items, hide_index=True, width="stretch")
        st.download_button("Download printable checklist",
                           views.checklist_markdown(data["name"], visit, items),
                           file_name=f"{data['name']}-{visit['name']}-checklist.md".replace(" ", "_"))
    with tab_burden:
        b = views.burden(table)
        c1, c2, c3 = st.columns(3)
        c1.metric("Visits", len(b))
        c2.metric("Procedures, all visits", int(b["Procedures"].sum()))
        busiest = str(b.loc[b["Procedures"].idxmax(), "Visit"]) if len(b) else "-"
        c3.metric("Busiest visit", f"Visit {busiest}" if busiest.isdigit() else busiest)
        long = b.assign(Other=b["Procedures"] - b["Needle / sample / dose"]).melt(
            id_vars="Visit", value_vars=["Needle / sample / dose", "Other"],
            var_name="Type", value_name="Count")
        st.altair_chart(alt.Chart(long).mark_bar().encode(
            y=alt.Y("Visit:N", sort=list(b["Visit"]), title=None, axis=alt.Axis(labelLimit=320)),
            x=alt.X("Count:Q", title="Procedures at this visit"),
            color=alt.Color("Type:N", sort=["Needle / sample / dose", "Other"],
                            scale=alt.Scale(range=["#c0392b", "#9db4cc"]),
                            legend=alt.Legend(orient="top", title=None)),
            order=alt.Order("Type:N", sort="ascending"),
            tooltip=["Visit", "Type", "Count"]), width="stretch")
        st.dataframe(b, hide_index=True, width="stretch")
        st.caption("Needle / sample / dose is a keyword match on assessment names "
                   "(blood, PK, biopsy, infusion and so on), so treat it as a rough count.")
    with tab_visits:
        st.dataframe(pd.DataFrame(table["visits"]), hide_index=True, width="stretch")
    with tab_json:
        st.json(table, expanded=False)

with main_soa:
    if not data["soa_done"]:
        st.write("Find the Schedule of Activities and turn it into a visit-by-assessment grid "
                 "with a page citation on every row.")
        if st.button("Extract the Schedule of Activities (about a minute)", type="primary"):
            extract_soa(data)
            st.rerun()
    elif not data["soa"]:
        st.warning("No Schedule of Activities table was found in this protocol.")
    else:
        soa_section(data)


# ---------------- Accuracy vs answer key ----------------
with main_acc:
    extracted = all_extracted_fields()
    st.write("Fill in the answer key by hand from the protocols, without looking at the app's "
             "output, then upload it to score every protocol extracted so far. "
             "Target from the stakeholder's plan: at least 90% of fields correct.")
    names = sorted(extracted) or [data["name"]]
    st.download_button("Download blank answer key (Excel)", answer_key.template_bytes(names),
                       file_name="answer-key-template.xlsx")
    key_file = st.file_uploader("Upload the filled answer key", type="xlsx", key="answer_key")
    if key_file:
        detail, summary = answer_key.score(extracted, pd.read_excel(key_file, sheet_name="Answer key"))
        if detail.empty:
            st.warning("No filled rows match a protocol extracted so far. Check that the Protocol "
                       "column matches the names here: " + ", ".join(sorted(extracted)))
        else:
            st.dataframe(summary, hide_index=True, width="stretch")
            st.dataframe(detail, hide_index=True, width="stretch")
            st.download_button("Download accuracy report (Excel)",
                               views.df_excel({"Summary": summary, "Detail": detail}),
                               file_name="accuracy-report.xlsx")


st.header("2. Ask about this protocol")
ask = {}
if data["fields"]:
    ask["study_fields"] = {F.LABELS[k]: {"value": v["value"], "page": v["page"]} for k, v in data["fields"].items()}
if data["design"]:
    ask["study_design"] = {k: v for k, v in data["design"].items() if k != "pages_read"}
if data["soa"]:
    ask["schedule_of_activities"] = data["soa"]
for turn in st.session_state.chat:
    st.chat_message(turn["role"]).write(turn["content"])

question = st.chat_input("e.g. What is the primary objective? At which visits is an ECG done?",
                         disabled=not ask)
if question:
    st.chat_message("user").write(question)
    with st.spinner("Thinking..."):
        reply, spent = soa.answer(ask, st.session_state.chat, question)
    st.session_state.spent += spent
    st.chat_message("assistant").write(reply)
    st.session_state.chat += [{"role": "user", "content": question},
                              {"role": "assistant", "content": reply}]
