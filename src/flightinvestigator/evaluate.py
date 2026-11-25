"""Golden-incident evaluation of the investigator (any LLM provider)."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import numpy as np
from flightlake import Lake

from .agents.graph import REPORT_SUBSYSTEM, investigate


def evaluate(lake_root: str, llm, toolbox, incidents_path: str = "data/incidents.json", limit: int = 0,
             out_dir: str = "results", name: str = "run") -> dict:
    lake = Lake(lake_root)
    golden = json.loads(Path(incidents_path).read_text())["golden_flights"]
    golden = golden[:limit] if limit else golden
    rows = []
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    with open(out / f"reports_{name}.jsonl", "w") as f:
        for fid in golden:
            faults = lake.summary(fid)["meta"].get("faults", [])
            truth = faults[0]["type"] if faults else "none"
            onset = faults[0]["t_start"] if faults else None
            rep = investigate(fid, llm, toolbox)
            f.write(json.dumps(rep, default=str) + "\n")
            n_ev = len(rep["evidence"]) + len(rep["rejected_evidence"])
            rows.append({
                "flight_id": fid, "truth": truth, "pred": rep["root_cause"],
                "subsystem_ok": REPORT_SUBSYSTEM[truth] == rep["subsystem"],
                "onset_err": abs(rep["onset_t_s"] - onset) if (onset is not None and rep["onset_t_s"] is not None
                                                               and rep["root_cause"] == truth) else None,
                "grounded_frac": len(rep["evidence"]) / n_ev if n_ev else None,
                "tool_calls": rep["trace"]["tool_calls"], "tokens": rep["trace"]["input_tokens"] +
                rep["trace"]["output_tokens"], "latency_s": rep["trace"]["latency_s"]})
            print(fid, truth, "->", rep["root_cause"], rep["confidence"])
    acc = np.mean([r["truth"] == r["pred"] for r in rows])
    per = {}
    for t in sorted({r["truth"] for r in rows}):
        rs = [r for r in rows if r["truth"] == t]
        per[t] = {"n": len(rs), "recall": round(float(np.mean([r["pred"] == t for r in rs])), 3)}
    onset = [r["onset_err"] for r in rows if r["onset_err"] is not None]
    gf = [r["grounded_frac"] for r in rows if r["grounded_frac"] is not None]
    res = {
        "name": name, "n": len(rows), "root_cause_acc": round(float(acc), 3),
        "subsystem_acc": round(float(np.mean([r["subsystem_ok"] for r in rows])), 3),
        "onset_mae_s": round(float(np.mean(onset)), 1) if onset else None,
        "onset_within_30s": round(float(np.mean(np.array(onset) <= 30)), 3) if onset else None,
        "evidence_grounded": round(float(np.mean(gf)), 3) if gf else None,
        "mean_tool_calls": round(float(np.mean([r["tool_calls"] for r in rows])), 1),
        "mean_tokens": round(float(np.mean([r["tokens"] for r in rows]))),
        "mean_latency_s": round(float(np.mean([r["latency_s"] for r in rows])), 2),
        "per_fault_recall": per,
        "confusions": Counter(f"{r['truth']}->{r['pred']}" for r in rows if r["truth"] != r["pred"]).most_common(8),
    }
    (out / f"eval_{name}.json").write_text(json.dumps(res, indent=2))
    return res
