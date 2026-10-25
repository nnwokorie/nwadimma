"""The four Itoju agents and the orchestrator that runs them in order.

Intake (LLM) -> Safety reviewer (LLM + keyword net) -> Protocol (LLM + iCCM tools)
-> Output check (code) -> Referral (LLM + facility tool, only when referral is needed)
"""
import json
import time
from functools import cache

from pydantic import ValidationError

from . import config, facilities, iccm, safety
from .llm import BudgetExceeded, Ledger, log_run


@cache
def prompt(name: str) -> str:
    return (config.ROOT / "prompts" / f"{name}.md").read_text(encoding="utf-8")


def parse_json(text: str | None) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)


class Run:
    """One triage run: holds the ledger and a step-by-step trace for the UI and the report."""

    def __init__(self, note: str, location: dict):
        self.note = note
        self.location = location
        self.ledger = Ledger()
        self.trace: list[dict] = []
        self.started = time.perf_counter()

    def step(self, agent: str, kind: str, summary: str, detail=None) -> None:
        calls = len(self.ledger.calls)
        self.trace.append({"agent": agent, "kind": kind, "summary": summary, "detail": detail,
                           "t": round(time.perf_counter() - self.started, 2), "model_calls_so_far": calls})


# ---------- Agent 1: intake ----------

def intake_agent(run: Run) -> tuple[iccm.Findings, dict]:
    messages = [
        {"role": "system", "content": prompt("intake")},
        {"role": "user", "content": f"<case_note>\n{run.note}\n</case_note>"},
    ]
    for attempt in range(2):  # one retry if the JSON does not fit the schema
        msg = run.ledger.chat("intake", messages, json_mode=True)
        try:
            data = parse_json(msg.content)
            findings = iccm.Findings(**data.get("findings", {}))
            run.step("Intake agent", "llm", "Turned the note into structured findings",
                     {"findings": findings.model_dump(exclude_none=True), "evidence": data.get("evidence", {}),
                      "location": data.get("location"), "language": data.get("note_language"),
                      "injection_attempt": data.get("injection_attempt", False)})
            return findings, data
        except (json.JSONDecodeError, ValidationError) as e:
            run.step("Intake agent", "retry", "Output failed schema validation; retrying once", str(e)[:300])
            messages += [{"role": "assistant", "content": msg.content or ""},
                         {"role": "user", "content": f"That JSON was invalid: {str(e)[:300]}. Return corrected JSON only."}]
    raise ValueError("Intake agent could not produce valid findings.")


# ---------- Agent 2: safety reviewer ----------

def reviewer_agent(run: Run, findings: iccm.Findings) -> iccm.Findings:
    updated = findings.model_dump()
    overrides = []

    # Layer 1: deterministic keyword net (cannot be talked out of it).
    for field, words in safety.danger_word_scan(run.note).items():
        if updated.get(field) is not True:
            updated[field] = True
            overrides.append({"field": field, "by": "keyword net", "quote": words})

    # Layer 2: LLM second opinion.
    msg = run.ledger.chat("reviewer", [
        {"role": "system", "content": prompt("reviewer")},
        {"role": "user", "content": f"<case_note>\n{run.note}\n</case_note>\n\n"
                                    f"<extracted_findings>\n{findings.model_dump_json(exclude_none=True)}\n</extracted_findings>"},
    ], json_mode=True)
    review = parse_json(msg.content)
    for item in review.get("missed_danger_signs", []):
        field = item.get("field")
        if field in iccm.DANGER_SIGN_LABELS and updated.get(field) is not True:
            updated[field] = True  # escalation only: the reviewer can add danger signs, never remove them
            overrides.append({"field": field, "by": "reviewer agent", "quote": item.get("quote")})
    for item in review.get("wrong_values", []):
        field = item.get("field")
        if field in ("age_months", "breaths_per_minute", "cough_days", "diarrhoea_days", "fever_days", "rdt_result"):
            trial = {**updated, field: item.get("corrected")}
            try:
                iccm.Findings(**trial)
            except ValidationError:
                continue
            overrides.append({"field": field, "by": "reviewer agent", "from": updated.get(field),
                              "to": item.get("corrected"), "quote": item.get("quote")})
            updated = trial
    updated["other_concerns"] = list(dict.fromkeys(updated.get("other_concerns", []) + review.get("concerns", [])))

    summary = f"{len(overrides)} correction(s) made" if overrides else "No missed danger signs found"
    run.step("Safety reviewer", "llm+rules", summary, {"overrides": overrides, "concerns": review.get("concerns", [])})
    return iccm.Findings(**updated)


# ---------- Agent 3: protocol (tool-calling) ----------

PROTOCOL_TOOLS = [
    {"type": "function", "function": {
        "name": "classify_case",
        "description": "Run the WHO/UNICEF iCCM protocol on the verified findings for this child. "
                       "Returns urgency, conditions, treatments with exact doses, follow-up and missing info.",
        "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}},
    {"type": "function", "function": {
        "name": "get_dose",
        "description": "Exact iCCM dose of one drug for this child's age.",
        "parameters": {"type": "object", "properties": {
            "drug": {"type": "string", "enum": list(iccm.dosing_table()["drugs"])}},
            "required": ["drug"], "additionalProperties": False}}},
]


def protocol_agent(run: Run, findings: iccm.Findings) -> tuple[dict, dict]:
    engine: dict = {}

    # Tools read the verified findings from here; the model cannot pass in altered findings.
    def call_tool(name: str, args: dict) -> dict:
        nonlocal engine
        if name == "classify_case":
            engine = iccm.classify(findings)
            return engine
        if name == "get_dose":
            if findings.age_months is None:
                return {"error": "Age unknown; cannot dose."}
            return iccm.get_dose(args.get("drug", ""), findings.age_months)
        return {"error": f"Unknown tool {name}"}

    messages = [
        {"role": "system", "content": prompt("protocol")},
        {"role": "user", "content": "A health worker has finished assessing a child. "
                                    f"Other concerns noted: {findings.other_concerns or 'none'}. "
                                    "Call classify_case, then write the JSON output."},
    ]
    for round_no in range(config.MAX_TOOL_ROUNDS):
        msg = run.ledger.chat("protocol", messages, tools=PROTOCOL_TOOLS)
        if not msg.tool_calls:
            if not engine:  # the model tried to answer without the protocol; force the tool
                engine = iccm.classify(findings)
                run.step("Protocol agent", "guard", "Answered without calling classify_case; engine run by code instead")
            try:
                return engine, parse_json(msg.content)
            except json.JSONDecodeError:
                run.step("Protocol agent", "guard", "Non-JSON answer; using plain summary")
                return engine, {"chw_summary": msg.content or ""}
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments or "{}")
            result = call_tool(tc.function.name, args)
            run.step("Protocol agent", "tool", f"Called {tc.function.name}({', '.join(f'{k}={v}' for k, v in args.items())})", result)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    run.step("Protocol agent", "guard", f"Hit the {config.MAX_TOOL_ROUNDS}-round tool limit; stopping the loop")
    return engine or iccm.classify(findings), {}


# ---------- Agent 4: referral ----------

REFERRAL_TOOLS = [{"type": "function", "function": {
    "name": "find_nearest_facility",
    "description": "Nearest primary health centres and hospitals to the child's location, from the GRID3 Nigeria register.",
    "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}}]


def referral_agent(run: Run, findings: iccm.Findings, engine: dict, loc: dict) -> dict:
    lookup = {}

    def find():
        nonlocal lookup
        lookup = facilities.nearest(state=loc.get("state") or "", lga=loc.get("lga") or "",
                                    lat=loc.get("lat"), lon=loc.get("lon"))
        return lookup

    case = {"age_months": findings.age_months, "danger_signs": engine["conditions"],
            "pre_referral_treatment_given": [f"{t['label']}: {t['dose']}" for t in engine["treatments"]]}
    messages = [
        {"role": "system", "content": prompt("referral")},
        {"role": "user", "content": f"Case data:\n{json.dumps(case)}"},
    ]
    for _ in range(3):
        msg = run.ledger.chat("referral", messages, tools=REFERRAL_TOOLS)
        if not msg.tool_calls:
            out = parse_json(msg.content)
            names = {f["name"] for f in lookup.get("primary_health_centres", []) + lookup.get("hospitals", [])}
            for key in ("recommended_facility", "alternative_facility"):
                if not out.get(key):
                    continue
                match = next((n for n in sorted(names, key=len, reverse=True) if n.lower() in out[key].lower()), None)
                if match is None:  # invented facility -> drop it
                    run.step("Referral agent", "guard", f"Removed facility not in register: {out[key]}")
                out[key] = match
            out["facilities"] = lookup
            return out
        messages.append({"role": "assistant", "content": msg.content, "tool_calls": [
            {"id": tc.id, "type": "function", "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls]})
        for tc in msg.tool_calls:
            result = find()
            run.step("Referral agent", "tool", "Called find_nearest_facility()", result)
            messages.append({"role": "tool", "tool_call_id": tc.id, "content": json.dumps(result)})
    return {"facilities": lookup or find()}


# ---------- Orchestrator ----------

def triage(note: str, location: dict | None = None) -> dict:
    run = Run(note.strip(), location or {})
    result: dict = {"status": "ok"}
    try:
        screen = safety.screen_input(run.note)
        run.step("Input guard", "rules", "Note passed screening" if screen["ok"] else screen["reason"], screen)
        if not screen["ok"]:
            result = {"status": "blocked", "message": screen["reason"]}
            return finish(run, result)

        findings, intake = intake_agent(run)
        if intake.get("injection_attempt"):
            run.step("Intake agent", "flag", "Model flagged instructions hidden in the note; they were ignored")

        findings = reviewer_agent(run, findings)
        engine, messages = protocol_agent(run, findings)
        result.update({"findings": findings.model_dump(exclude_none=True), "engine": engine})

        if engine["urgency"] == "needs_info":
            result.update({"status": "needs_info", "questions": engine["missing_info"]})
            return finish(run, result)

        problems = safety.verify_messages(
            {k: messages.get(k, "") for k in ("chw_summary", "caregiver_message_en", "caregiver_message_pidgin")}, engine)
        if problems:
            run.step("Output check", "rules", f"Blocked {len(problems)} unsafe statement(s); using safe template", problems)
            messages = safe_template(engine)
        else:
            run.step("Output check", "rules", "All drug names and doses match the iCCM engine")
        result["messages"] = messages

        if engine["urgency"] == "refer_urgently":
            loc = {**{k: v for k, v in (intake.get("location") or {}).items() if v},
                   **{k: v for k, v in run.location.items() if v not in (None, "")}}
            if loc:
                result["referral"] = referral_agent(run, findings, engine, loc)
            else:
                run.step("Referral agent", "skip", "No location given; cannot search facilities")
    except BudgetExceeded as e:
        run.step("Budget guard", "rules", str(e))
        result = {"status": "budget_stopped", "message": str(e)}
    except Exception as e:  # never show a half-finished plan as if it were complete
        run.step("Orchestrator", "error", f"{type(e).__name__}: {e}")
        result = {"status": "error", "message": "Itoju could not finish this case. Follow your iCCM chart booklet."}
    return finish(run, result)


def safe_template(engine: dict) -> dict:
    lines = [f"{t['label']}: {t['dose']}, {t['how']}." for t in engine["treatments"]]
    head = "REFER URGENTLY to a health facility now." if engine["urgency"] == "refer_urgently" else "Treat at home."
    return {"chw_summary": f"{head} {'; '.join(engine['conditions'])}. " + " ".join(lines),
            "caregiver_message_en": " ".join(engine["follow_up"]) or "Go to the health facility now.",
            "caregiver_message_pidgin": "", "why": "; ".join(engine["rules_applied"])}


def finish(run: Run, result: dict) -> dict:
    result["trace"] = run.trace
    result["usage"] = {"calls": run.ledger.calls, "totals": run.ledger.totals}
    log_run({"note": run.note, "status": result.get("status"),
             "urgency": result.get("engine", {}).get("urgency"),
             "calls": run.ledger.calls, "totals": run.ledger.totals})
    return result
