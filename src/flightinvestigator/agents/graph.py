"""Deterministic multi-agent investigation graph.

            +-----------+      suspected subsystems (<=2)     +----------------------+
 flight --> |  TRIAGE   | ----------------------------------> | SPECIALIST(s)        |
            | overview, |                                      | propulsion & power   |
            | messages, |                                      | navigation (GNSS/EKF)|
            | deviation |                                      | air data & sensors   |
            | scan, sim.|                                      | flight controls      |
            | incidents |                                      +----------+-----------+
            +-----------+                                                 | findings + cited tool calls
                                                                          v
                                                    +-------------------------------------+
                                                    | VERIFIER (deterministic grounding)  |
                                                    | every number in a claim must appear |
                                                    | in the cited tool output            |
                                                    +------------------+------------------+
                                                                       v
                                                               REPORTER -> JSON + Markdown

The *routing* is fixed code (auditable, reproducible); LLMs only reason inside nodes, with a
scoped tool set and a hard step budget. Every tool call is logged with an id so each claim in
the final report is traceable to raw evidence.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

from ..specs import tool_specs

SUBSYSTEM_FAULTS = {
    "propulsion_power": ["motor_failure", "generator_dropout", "battery_degradation"],
    "navigation": ["gnss_jamming", "gnss_spoofing"],
    "sensors": ["pitot_blockage", "imu_vibration"],
    "flight_controls": ["elevator_stuck"],
}
FAULT_SUBSYSTEM = {f: s for s, fs in SUBSYSTEM_FAULTS.items() for f in fs}
REPORT_SUBSYSTEM = {"motor_failure": "propulsion", "generator_dropout": "power", "battery_degradation": "power",
                    "gnss_jamming": "navigation", "gnss_spoofing": "navigation", "pitot_blockage": "sensors",
                    "imu_vibration": "sensors", "elevator_stuck": "flight_controls", "none": "none"}
ACTION_KEYWORDS = {"gnss_jamming": "NOTAM|interference|drift|CRPA|outage", "gnss_spoofing": "spoof|gate|range safety",
                   "imu_vibration": "vibration|propeller|mount", "pitot_blockage": "pitot",
                   "elevator_stuck": "elevator", "battery_degradation": "battery", "generator_dropout": "generator",
                   "motor_failure": "motor|ESC"}
TRIAGE_TOOLS = ["get_flight_overview", "get_messages", "scan_deviations", "find_similar_incidents", "compare_to_fleet"]
SPECIALIST_TOOLS = ["get_channel_stats", "get_timeseries", "check_relation", "get_messages", "search_knowledge_base",
                    "compare_to_fleet"]

TRIAGE_SYSTEM = """ROLE: triage
You are the triage lead of a flight-test data investigation for a hybrid-electric STOL aircraft (4 DEP motors,
battery + range-extender generator, GNSS/EKF navigation, pitot air data, elevator).
Use the tools to get the flight overview, warning messages, the fleet-envelope deviation scan and similar past
incidents. Waypoint turns (roll up to 25 deg, yaw rate up to 10 deg/s) are normal.
Decide which subsystems specialists must examine: propulsion_power, navigation, sensors, flight_controls.
Finish with ONLY a JSON object:
{"nominal": bool, "suspected_subsystems": [<=2 names, most likely first], "hypotheses": [{"fault_type": str,
"rationale": str}], "focus_window": [t0, t1] or null}"""

SPECIALIST_SYSTEM = """ROLE: specialist:{subsystem}
You are the {subsystem} specialist. Candidate faults: {faults}, or "none".
Triage context: {context}
Investigate with the tools (physics-consistency checks, channel stats, time series, knowledge base). Be
quantitative and cite evidence: every claim must quote numbers exactly as returned by a tool and reference that
tool call's id. Finish with ONLY a JSON object:
{{"fault_type": one of {faults} or "none", "confidence": 0..1, "onset_t_s": number or null,
"evidence": [{{"claim": str, "tool_call_id": str}}], "notes": str}}"""

REPORT_SYSTEM = """ROLE: reporter
Write a 3-4 sentence flight-test debrief summary for engineers from the verified findings JSON. Use only numbers
present in the JSON. Plain text, no preamble."""


@dataclass
class Trace:
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0, "llm_calls": 0})
    agents: list[dict] = field(default_factory=list)

    def add_usage(self, u):
        self.usage["input_tokens"] += u.get("input_tokens", 0)
        self.usage["output_tokens"] += u.get("output_tokens", 0)
        self.usage["llm_calls"] += 1


def extract_json(text: str) -> dict | None:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        try:
            return json.loads(re.sub(r",\s*([}\]])", r"\1", m.group(0)))
        except json.JSONDecodeError:
            return None


def run_agent(llm, system: str, user: str, tool_names: list[str], toolbox, trace: Trace, agent: str,
              max_steps: int = 8) -> tuple[dict | None, list[dict]]:
    tools = tool_specs(tool_names)
    transcript = [{"role": "user", "text": user}]
    calls_here = []
    for step in range(max_steps + 1):
        if step == max_steps:
            transcript.append({"role": "user", "text": "Tool budget exhausted. Respond now with ONLY the JSON."})
        r = llm.complete(system, transcript, tools if step < max_steps else [])
        trace.add_usage(r.usage)
        transcript.append({"role": "assistant", "text": r.text, "tool_calls": r.tool_calls})
        if not r.tool_calls:
            out = extract_json(r.text)
            if out is None and step < max_steps:
                transcript.append({"role": "user", "text": "Respond with ONLY the JSON object."})
                continue
            trace.agents.append({"agent": agent, "steps": step, "output": out})
            return out, calls_here
        for c in r.tool_calls:
            t0 = time.time()
            if c["name"] not in tool_names:
                content = json.dumps({"error": f"tool {c['name']} not available to this agent"})
            else:
                try:
                    content = json.dumps(toolbox.call(c["name"], c["args"]), default=str)
                except Exception as e:  # tool errors go back to the model so it can self-correct
                    content = json.dumps({"error": f"{type(e).__name__}: {e}"[:300]})
            content = content[:6000]
            rec = {"id": c["id"], "agent": agent, "name": c["name"], "args": c["args"], "content": content,
                   "latency_ms": round(1000 * (time.time() - t0))}
            trace.tool_calls.append(rec)
            calls_here.append(rec)
            transcript.append({"role": "tool", "id": c["id"], "name": c["name"], "content": content})
    return None, calls_here


# ---------------------------------------------------------------- verifier
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def verify_claim(claim: str, sources: list[str]) -> tuple[bool, list[str]]:
    """A claim is grounded when every non-trivial number in it appears (within 1%) in a cited tool output."""
    src_nums = [float(x) for s in sources for x in NUM.findall(s)]
    missing = []
    for x in NUM.findall(claim):
        v = float(x)
        if abs(v) <= 4 and v == int(v):  # motor index, small counts
            continue
        if not any(abs(v - s) <= max(0.05, 0.01 * abs(v)) for s in src_nums):
            missing.append(x)
    has_numbers = any(not (abs(float(x)) <= 4 and float(x) == int(float(x))) for x in NUM.findall(claim))
    return has_numbers and not missing, missing


def verify_finding(f: dict, calls: list[dict]) -> dict:
    by_id = {c["id"]: c["content"] for c in calls}
    ev_out, ok = [], 0
    for ev in f.get("evidence", []) or []:
        claim = str(ev.get("claim", ""))
        cid = ev.get("tool_call_id")
        srcs = [by_id[cid]] if cid in by_id else list(by_id.values())
        g, missing = verify_claim(claim, srcs)
        ok += g
        ev_out.append({"claim": claim, "tool_call_id": cid, "grounded": g, "unverified_numbers": missing})
    n = len(ev_out)
    frac = ok / n if n else 0.0
    conf = float(f.get("confidence", 0) or 0)
    score = conf * (0.5 + 0.5 * frac) if ok else conf * 0.3
    return {**f, "evidence": ev_out, "grounded_fraction": round(frac, 2), "verified_score": round(score, 3)}


# ---------------------------------------------------------------- the graph
def investigate(flight_id: str, llm, toolbox, max_steps: int = 8, narrative: bool = True) -> dict:
    t_start = time.time()
    trace = Trace()
    triage, tcalls = run_agent(llm, TRIAGE_SYSTEM, f"Investigate flight {flight_id}.", TRIAGE_TOOLS, toolbox,
                               trace, "triage", max_steps)
    triage = triage or {"nominal": False, "suspected_subsystems": list(SUBSYSTEM_FAULTS)[:2], "hypotheses": []}
    subs = [s for s in triage.get("suspected_subsystems", []) if s in SUBSYSTEM_FAULTS][:2]
    findings = []
    if not triage.get("nominal") or subs:
        ctx = json.dumps({k: triage.get(k) for k in ("hypotheses", "focus_window")})[:1500]
        for sub in subs:
            faults = SUBSYSTEM_FAULTS[sub]
            sysmsg = SPECIALIST_SYSTEM.format(subsystem=sub, faults=json.dumps(faults), context=ctx)
            out, scalls = run_agent(llm, sysmsg, f"Flight {flight_id}: examine the {sub} subsystem.",
                                    SPECIALIST_TOOLS, toolbox, trace, f"specialist:{sub}", max_steps)
            if out:
                out["subsystem"] = sub
                if out.get("fault_type") not in faults:
                    out["fault_type"] = "none"
                findings.append(verify_finding(out, scalls))
    faulty = [f for f in findings if f["fault_type"] != "none"]
    best = max(faulty, key=lambda f: f["verified_score"]) if faulty else None
    if best is not None and best["verified_score"] < 0.3:
        best = None
    root = best["fault_type"] if best else "none"
    onset = best.get("onset_t_s") if best else None
    overview = next((json.loads(c["content"]) for c in tcalls if c["name"] == "get_flight_overview"), {})
    phase = None
    if onset is not None:
        for p in overview.get("phases", []):
            if p["t_start"] is not None and p["t_start"] <= onset <= (p["t_end"] or 1e9) + 0.1:
                phase = p["phase"]
    similar = next((json.loads(c["content"]).get("incidents", []) for c in tcalls
                    if c["name"] == "find_similar_incidents"), [])
    actions = []
    if root != "none":
        kb = toolbox.call("search_knowledge_base", {"query": f"{root.replace('_', ' ')} actions", "k": 3})
        key = ACTION_KEYWORDS.get(root, root.split("_")[0])
        for r in kb.get("results", []):
            for ln in r["text"].splitlines():
                ln = ln.strip()
                if ln.startswith(("- ", "1.", "2.", "3.", "4.")) and re.search(key, ln, re.I):
                    actions.append(re.sub(r"^(- |\d\. )", "", ln))
        actions = list(dict.fromkeys(actions))[:4]
    report = {
        "flight_id": flight_id, "root_cause": root, "subsystem": REPORT_SUBSYSTEM[root],
        "confidence": best["verified_score"] if best else round(1 - max([f["verified_score"] for f in findings] or [0]), 2),
        "onset_t_s": onset, "phase_at_onset": phase,
        "evidence": [e for e in (best or {}).get("evidence", []) if e["grounded"]],
        "rejected_evidence": [e for f in findings for e in f["evidence"] if not e["grounded"]],
        "findings": findings, "triage": triage, "similar_incidents": similar, "recommended_actions": actions,
    }
    if narrative:
        r = llm.complete(REPORT_SYSTEM, [{"role": "user", "text": json.dumps(
            {k: report[k] for k in ("root_cause", "onset_t_s", "phase_at_onset", "confidence", "evidence",
                                    "similar_incidents")}, default=str)}], [])
        trace.add_usage(r.usage)
        report["summary"] = r.text.strip()
    report["trace"] = {"tool_calls": len(trace.tool_calls), **trace.usage, "latency_s": round(time.time() - t_start, 2),
                       "calls": [{k: v for k, v in c.items() if k != "content"} for c in trace.tool_calls]}
    return report


def to_markdown(r: dict) -> str:
    lines = [f"# Flight investigation - {r['flight_id']}", "",
             f"**Root cause:** `{r['root_cause']}` ({r['subsystem']}) - confidence {r['confidence']}",
             f"**Onset:** {r['onset_t_s']} s ({r['phase_at_onset']})", "", r.get("summary", ""), "", "## Evidence"]
    lines += [f"- {e['claim']} _(tool call `{e['tool_call_id']}`)_" for e in r["evidence"]] or ["- none"]
    if r["rejected_evidence"]:
        lines += ["", "## Claims rejected by the verifier"]
        lines += [f"- ~~{e['claim']}~~ (unverified: {', '.join(e['unverified_numbers']) or 'no numbers'})"
                  for e in r["rejected_evidence"]]
    lines += ["", "## Similar past incidents"]
    lines += [f"- {i['flight_id']} (sim {i['similarity']}): {i['root_cause']} - {i['resolution']}"
              for i in r["similar_incidents"]] or ["- none"]
    lines += ["", "## Recommended actions"] + [f"- {a}" for a in r["recommended_actions"]]
    t = r["trace"]
    lines += ["", f"_{t['tool_calls']} tool calls, {t['llm_calls']} LLM calls, "
                  f"{t['input_tokens']}+{t['output_tokens']} tokens, {t['latency_s']} s_"]
    return "\n".join(lines)
