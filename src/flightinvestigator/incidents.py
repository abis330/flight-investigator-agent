"""Build the 'closed investigations' database used for similar-incident retrieval.

In production this comes from the defect/ticket system. Here, flights in the *history* split
with a confirmed root cause become incidents; the golden evaluation flights are excluded so
retrieval can never leak the answer.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from flightlake import Lake

from .tools import fit_baseline, scan, signature

RESOLUTION = {
    "motor_failure": "ESC replaced after desync; run-up test passed",
    "generator_dropout": "range-extender ECU fault cleared; fuel line clamp replaced",
    "battery_degradation": "weak cell group found by impedance test; module swapped",
    "gnss_jamming": "range interference confirmed; test card moved",
    "gnss_spoofing": "spoofing reported to range safety; innovation gate tightened",
    "imu_vibration": "prop rebalanced and motor mount re-torqued",
    "pitot_blockage": "insect nest removed from pitot; covers mandatory on ground",
    "elevator_stuck": "servo linkage binding fixed; hinge replaced",
}


def split_of(fid: str) -> str:
    h = int(hashlib.md5(fid.encode()).hexdigest()[:8], 16) / 0xFFFFFFFF
    return "golden" if h < 0.30 else "history"


def build(lake_root: str, out: str = "data/incidents.json") -> dict:
    lake = Lake(lake_root)
    cat = lake.catalog()
    hist = [f for f in cat.flight_id if split_of(f) == "history"]
    nominal = [f for f in hist if cat.set_index("flight_id").loc[f, "injected_faults"] == "none"]
    baseline = fit_baseline(lake, nominal[:60])
    Path(out).parent.mkdir(parents=True, exist_ok=True)
    Path(out).with_name("fleet_baseline.json").write_text(json.dumps(baseline))
    incidents = []
    for fid in hist:
        faults = lake.summary(fid)["meta"].get("faults", [])
        if not faults:
            continue
        sig = signature(scan(lake.telemetry(fid), baseline, 4.0, top=25))
        rc = faults[0]["type"]
        incidents.append({"flight_id": fid, "root_cause": rc, "resolution": RESOLUTION[rc], "signature": sig})
    golden = sorted(f for f in cat.flight_id if split_of(f) == "golden")
    db = {"incidents": incidents, "golden_flights": golden}
    Path(out).write_text(json.dumps(db))
    return {"incidents": len(incidents), "golden": len(golden), "baseline_flights": len(nominal[:60])}
