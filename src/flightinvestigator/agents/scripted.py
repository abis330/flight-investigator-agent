"""ScriptedLLM: a deterministic, rule-based stand-in for the LLM.

It speaks exactly the same tool-calling protocol as the real providers, so the full graph
(triage -> specialists -> verifier -> reporter), the MCP transport and the evaluation harness
run offline in CI with no API key. It also serves as an *expert-rules baseline*: the LLM
agents must beat it on the golden incident set to justify their cost.
"""
from __future__ import annotations

import json
import re

from .llm import LLMResponse

CHANNEL_SUBSYSTEM = {
    "motor1_rpm": "propulsion_power", "motor2_rpm": "propulsion_power", "motor3_rpm": "propulsion_power",
    "motor4_rpm": "propulsion_power", "batt_v": "propulsion_power", "batt_a": "propulsion_power",
    "batt_temp_c": "propulsion_power", "gen_kw": "propulsion_power", "gnss_nsat": "navigation",
    "gnss_hdop": "navigation", "ekf_pos_innov_m": "navigation", "ekf_vel_innov_mps": "navigation",
    "ekf_hacc_m": "navigation", "airspeed_mps": "sensors", "vibe_mps2": "sensors", "elevator_cmd_deg": "flight_controls",
    "elevator_pos_deg": "flight_controls", "q_dps": "flight_controls",
}
MSG_SUBSYSTEM = [(r"ESC|motor|Generator|Battery", "propulsion_power"), (r"GNSS|GPS", "navigation"),
                 (r"Airspeed|vibration", "sensors"), (r"Elevator", "flight_controls")]

PLANS = {
    "propulsion_power": [("check_relation", {"relation": "throttle_vs_rpm"}),
                         ("check_relation", {"relation": "power_balance"}),
                         ("compare_to_fleet", {"metric": "min_batt_v"}),
                         ("compare_to_fleet", {"metric": "max_batt_temp_c"})],
    "navigation": [("check_relation", {"relation": "gnss_health_vs_innovation"})],
    "sensors": [("check_relation", {"relation": "airspeed_vs_groundspeed"}),
                ("compare_to_fleet", {"metric": "p95_vibe_mps2"})],
    "flight_controls": [("check_relation", {"relation": "elevator_cmd_vs_pos"})],
}


class ScriptedLLM:
    def __init__(self):
        self._n = 0

    def _call(self, name, args):
        self._n += 1
        return LLMResponse("", [{"id": f"call_{self._n:04d}", "name": name, "args": args}],
                           {"input_tokens": 0, "output_tokens": 0})

    def complete(self, system: str, transcript: list[dict], tools: list[dict]) -> LLMResponse:
        role = re.search(r"ROLE: (\S+)", system).group(1)
        first = transcript[0]["text"]
        m = re.search(r"[Ff]light ([\w-]+?)[.:]", first)
        fid = m.group(1) if m else None
        results = {}
        calls = {}
        for ev in transcript:
            if ev["role"] == "assistant":
                for c in ev.get("tool_calls", []):
                    calls[c["id"]] = c
            if ev["role"] == "tool":
                results[ev["id"]] = (calls[ev["id"]], json.loads(ev["content"]))
        if role == "reporter":
            return LLMResponse(self._narrative(json.loads(first)))
        if role == "triage":
            plan = [("get_flight_overview", {}), ("get_messages", {"min_level": "WARNING"}), ("scan_deviations", {}),
                    ("find_similar_incidents", {"k": 3})]
            if len(results) < len(plan) and tools:
                n, a = plan[len(results)]
                return self._call(n, {"flight_id": fid, **a})
            return LLMResponse(json.dumps(self._triage(results)))
        sub = role.split(":", 1)[1]
        plan = PLANS[sub]
        if len(results) < len(plan) and tools:
            n, a = plan[len(results)]
            return self._call(n, {"flight_id": fid, **a})
        return LLMResponse(json.dumps(self._specialist(sub, results)))

    # ------------------------------------------------------------------ triage rules
    def _triage(self, results) -> dict:
        votes: dict[str, float] = {}
        hyps = []
        for call, res in results.values():
            if call["name"] == "scan_deviations":
                for d in res.get("deviations", []):
                    s = CHANNEL_SUBSYSTEM.get(d["channel"])
                    if s:
                        votes[s] = votes.get(s, 0) + min(abs(d["peak_z"]), 30) / 10
            if call["name"] == "get_messages":
                for m in res.get("messages", []):
                    for pat, s in MSG_SUBSYSTEM:
                        if re.search(pat, m["text"], re.I):
                            votes[s] = votes.get(s, 0) + 3
            if call["name"] == "find_similar_incidents":
                for inc in res.get("incidents", [])[:3]:
                    from .graph import FAULT_SUBSYSTEM

                    s = FAULT_SUBSYSTEM.get(inc["root_cause"])
                    if s and inc["similarity"] > 0.5:
                        votes[s] = votes.get(s, 0) + 2 * inc["similarity"]
                        hyps.append({"fault_type": inc["root_cause"],
                                     "rationale": f"similar to {inc['flight_id']} ({inc['similarity']})"})
        ranked = sorted(votes, key=lambda s: -votes[s])
        subs = [s for s in ranked if votes[s] >= 1.5][:2]
        return {"nominal": not subs, "suspected_subsystems": subs, "hypotheses": hyps[:3], "focus_window": None}

    # ------------------------------------------------------------------ specialist rules
    def _specialist(self, sub, results) -> dict:
        r = {}
        for cid, (call, res) in results.items():
            key = call["args"].get("relation") or call["args"].get("metric") or call["name"]
            r[key] = (cid, res)
        none = {"fault_type": "none", "confidence": 0.6, "onset_t_s": None, "evidence": [], "notes": "no breach"}

        def ev(key, text):
            return {"claim": text, "tool_call_id": r[key][0]}

        if sub == "propulsion_power":
            cid, rpm = r["throttle_vs_rpm"]
            if rpm.get("verdict") == "INCONSISTENT":
                m = rpm["breach_motor"]
                return {"fault_type": "motor_failure", "confidence": 0.9, "onset_t_s": rpm["breach_start_t"],
                        "evidence": [ev("throttle_vs_rpm", f"motor {m} rpm ratio fell to {rpm[f'motor{m}_ratio_min']} "
                                                           f"of expected from t={rpm['breach_start_t']} s")],
                        "notes": "rpm/throttle relation broken on one motor"}
            _, pb = r["power_balance"]
            if pb.get("gen_off_start_t") is not None:
                return {"fault_type": "generator_dropout", "confidence": 0.85, "onset_t_s": pb["gen_off_start_t"],
                        "evidence": [ev("power_balance", f"generator output averaged {pb['gen_kw_mean_climb_cruise']} kW in "
                                                         f"climb/cruise and was off from t={pb['gen_off_start_t']} s; "
                                                         f"battery supplied {pb['batt_kw_mean_climb_cruise']} kW")],
                        "notes": ""}
            _, v = r["min_batt_v"]
            _, tmp = r["max_batt_temp_c"]
            if (v.get("percentile") or 100) <= 12:
                e = [ev("min_batt_v", f"min battery voltage {v['value']} V is at fleet percentile {v['percentile']} "
                                      f"(fleet median {v['fleet_median']} V)")]
                if (tmp.get("percentile") or 0) >= 80:
                    e.append(ev("max_batt_temp_c", f"max battery temperature {tmp['value']} C at percentile "
                                                   f"{tmp['percentile']}"))
                return {"fault_type": "battery_degradation", "confidence": 0.6, "onset_t_s": None, "evidence": e,
                        "notes": "voltage low vs fleet; onset not localised"}
            return none
        if sub == "navigation":
            _, g = r["gnss_health_vs_innovation"]
            if g.get("satellites_lost_start_t") is not None:
                return {"fault_type": "gnss_jamming", "confidence": 0.9, "onset_t_s": g["satellites_lost_start_t"],
                        "evidence": [ev("gnss_health_vs_innovation",
                                        f"satellites dropped to {g['nsat_min']} with hdop {g['hdop_max']} from "
                                        f"t={g['satellites_lost_start_t']} s; EKF accuracy grew to {g['hacc_max']} m")],
                        "notes": ""}
            if g.get("innov_high_while_healthy_start_t") is not None:
                t = g["innov_high_while_healthy_start_t"]
                return {"fault_type": "gnss_spoofing", "confidence": 0.85, "onset_t_s": t,
                        "evidence": [ev("gnss_health_vs_innovation",
                                        f"position innovation reached {g['pos_innov_max']} m while satellites stayed "
                                        f">= {g['nsat_min']} (innovation high from t={t} s)")], "notes": ""}
            return none
        if sub == "sensors":
            _, a = r["airspeed_vs_groundspeed"]
            if a.get("verdict") == "INCONSISTENT":
                return {"fault_type": "pitot_blockage", "confidence": 0.85, "onset_t_s": a["breach_start_t"],
                        "evidence": [ev("airspeed_vs_groundspeed", f"airspeed minus groundspeed reached "
                                                                   f"{a['residual_min']} m/s (breach from "
                                                                   f"t={a['breach_start_t']} s)")], "notes": ""}
            _, vb = r["p95_vibe_mps2"]
            if (vb.get("percentile") or 0) >= 90:
                return {"fault_type": "imu_vibration", "confidence": 0.7, "onset_t_s": None,
                        "evidence": [ev("p95_vibe_mps2", f"p95 vibration {vb['value']} m/s2 at fleet percentile "
                                                         f"{vb['percentile']} (fleet p95 {vb['fleet_p95']})")],
                        "notes": ""}
            return none
        if sub == "flight_controls":
            _, e = r["elevator_cmd_vs_pos"]
            if e.get("verdict") == "INCONSISTENT":
                return {"fault_type": "elevator_stuck", "confidence": 0.9, "onset_t_s": e["breach_start_t"],
                        "evidence": [ev("elevator_cmd_vs_pos", f"elevator tracking error up to {e['max_err_deg']} deg "
                                                               f"(RMSE {e['rmse_deg']} deg) from t={e['breach_start_t']} s")],
                        "notes": ""}
            return none
        return none

    @staticmethod
    def _narrative(f: dict) -> str:
        if f["root_cause"] == "none":
            return "No fault was confirmed: specialists found all physics-consistency checks within limits."
        ev = "; ".join(e["claim"] for e in f["evidence"][:2])
        sim = f["similar_incidents"][0]["flight_id"] if f.get("similar_incidents") else "none"
        when = (f"onset around t={f['onset_t_s']} s during {f['phase_at_onset']}" if f["onset_t_s"] is not None
                else "onset not localised in time")
        return (f"Root cause assessed as {f['root_cause']} (confidence {f['confidence']}), {when}. "
                f"Evidence: {ev}. Most similar past incident: {sim}.")
