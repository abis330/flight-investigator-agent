"""Deterministic, read-only diagnostic tools over the flight lake.

These are the *only* way agents touch data. Every tool:
  * is read-only and bounded (row limits, time windows, downsampling)
  * returns compact JSON-serialisable dicts with rounded numbers (cheap in tokens, easy to verify)
  * never exposes test-card ground truth (injected faults are hidden from the agent)
The same functions are served over MCP (mcp_server.py) and can be called in-process.
"""
from __future__ import annotations

import json
import math
import os
import re
from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd
from flightlake import Lake
from flightlake.sqlguard import guard

HIDDEN_CATALOG_COLS = {"injected_faults", "fault_onset_s", "has_labels"}
AIR = ["climb", "cruise", "descent", "approach"]
DEV_CHANNELS = ["airspeed_mps", "groundspeed_mps", "vz_mps", "roll_deg", "pitch_deg", "q_dps", "r_dps", "vibe_mps2",
                "throttle", "elevator_cmd_deg", "elevator_pos_deg", "motor1_rpm", "motor2_rpm", "motor3_rpm",
                "motor4_rpm", "batt_v", "batt_a", "batt_temp_c", "gen_kw", "gnss_nsat", "gnss_hdop",
                "ekf_pos_innov_m", "ekf_vel_innov_mps", "ekf_hacc_m"]


def _r(x, nd=2):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return x
    return None if math.isnan(x) or math.isinf(x) else round(x, nd)


class FlightTools:
    def __init__(self, lake_root: str | None = None, kb_dir: str | None = None, incidents_path: str | None = None,
                 hidden_flights: set[str] | None = None):
        self.lake = Lake(lake_root or os.environ.get("FLIGHTLAKE_ROOT", "data/lake"))
        self.kb = KnowledgeBase(kb_dir or os.environ.get("KB_DIR", str(Path(__file__).parents[2] / "kb")))
        self.incidents_path = incidents_path or os.environ.get("INCIDENTS_PATH", "data/incidents.json")
        self.hidden = hidden_flights or set()
        self._baseline = None

    # ------------------------------------------------------------ fleet / flight level
    def list_flights(self, site: str | None = None, min_qc: float = 0.0, limit: int = 20) -> dict:
        """List flights in the fleet catalog (no ground-truth columns)."""
        cat = self.lake.catalog()
        cat = cat[[c for c in cat.columns if c not in HIDDEN_CATALOG_COLS]]
        if site:
            cat = cat[cat.site == site]
        cat = cat[cat.qc_score >= min_qc]
        cols = ["flight_id", "date", "site", "firmware", "qc_score", "duration_s", "n_warnings", "n_errors"]
        return {"n_total": int(len(cat)), "flights": cat[cols].head(limit).round(2).to_dict(orient="records")}

    def get_flight_overview(self, flight_id: str) -> dict:
        """Metadata, phase timeline, key metrics and data-quality summary for one flight."""
        s = self.lake.summary(flight_id)
        meta = {k: s["meta"].get(k) for k in ("airframe", "firmware", "site", "date", "wind_speed", "wind_dir_deg")}
        qc = {k: s["qc"][k] for k in ("qc_score", "channels_present", "gaps", "spikes_removed")}
        return {"flight_id": flight_id, "meta": {k: _r(v) for k, v in meta.items()},
                "phases": [{"phase": p["phase"], "t_start": _r(p["t_start"], 1), "t_end": _r(p["t_end"], 1)}
                           for p in s["phases"]],
                "metrics": {k: _r(v) for k, v in s["metrics"].items()}, "qc": qc}

    def get_messages(self, flight_id: str, min_level: str = "WARNING", t0: float | None = None,
                     t1: float | None = None, limit: int = 30) -> dict:
        """Autopilot text messages. min_level: INFO | WARNING | ERROR."""
        order = {"DEBUG": 0, "INFO": 1, "NOTICE": 1, "WARNING": 2, "ERROR": 3, "CRITICAL": 4}
        m = self.lake.messages(flight_id)
        m = m[m.level.str.upper().map(order).fillna(1) >= order.get(min_level.upper(), 2)]
        if t0 is not None:
            m = m[m.t_s >= t0]
        if t1 is not None:
            m = m[m.t_s <= t1]
        return {"messages": [{"t_s": _r(r.t_s, 1), "level": r.level, "text": r.text} for r in m.head(limit).itertuples()]}

    # ------------------------------------------------------------ channel level
    def get_channel_stats(self, flight_id: str, channels: list[str], t0: float | None = None,
                          t1: float | None = None) -> dict:
        """min / mean / max / std / first / last of channels in a time window."""
        tel = self.lake.telemetry(flight_id, channels, t0, t1)
        out = {}
        for c in channels:
            if c not in tel:
                out[c] = {"error": "unknown channel"}
                continue
            s = tel[c]
            v = s.dropna()
            out[c] = {"min": _r(v.min()), "mean": _r(v.mean()), "max": _r(v.max()), "std": _r(v.std()),
                      "first": _r(v.iloc[0]) if len(v) else None, "last": _r(v.iloc[-1]) if len(v) else None,
                      "missing_pct": _r(100 * s.isna().mean(), 1)}
        return {"window": [_r(tel.t_s.min(), 1), _r(tel.t_s.max(), 1)], "stats": out}

    def get_timeseries(self, flight_id: str, channels: list[str], t0: float, t1: float, max_points: int = 40) -> dict:
        """Downsampled time series (bucket means) for up to 4 channels."""
        channels = channels[:4]
        tel = self.lake.telemetry(flight_id, channels, t0, t1)
        if tel.empty:
            return {"error": "no data in window"}
        b = np.linspace(tel.t_s.min(), tel.t_s.max() + 1e-6, min(max_points, len(tel)) + 1)
        g = tel.groupby(pd.cut(tel.t_s, b), observed=True)[["t_s", *channels]].mean()
        return {"t_s": [_r(x, 1) for x in g.t_s], **{c: [_r(x) for x in g[c]] for c in channels}}

    # ------------------------------------------------------------ diagnostics
    def _fleet_baseline(self) -> dict:
        if self._baseline is None:
            path = Path(self.incidents_path).with_name("fleet_baseline.json")
            if path.exists():
                self._baseline = json.loads(path.read_text())
            else:
                cat = self.lake.catalog()
                nominal = [f for f in cat.flight_id if f not in self.hidden][:60]
                self._baseline = fit_baseline(self.lake, nominal)
        return self._baseline

    def scan_deviations(self, flight_id: str, z_threshold: float = 4.0, top: int = 10) -> dict:
        """Fault-agnostic scan: channel segments outside the fleet-normal envelope for the same phase."""
        return {"deviations": scan(self.lake.telemetry(flight_id), self._fleet_baseline(), z_threshold, top=top),
                "note": "z = robust z-score vs nominal fleet in same phase; turns (roll/yaw) are usually benign"}

    def check_relation(self, flight_id: str, relation: str, t0: float | None = None, t1: float | None = None) -> dict:
        """Physics-consistency check. relation in: airspeed_vs_groundspeed, elevator_cmd_vs_pos, throttle_vs_rpm,
        power_balance, gnss_health_vs_innovation."""
        tel = self.lake.telemetry(flight_id, None, t0, t1)
        tel = tel[tel.phase_est.isin(AIR)]
        if tel.empty:
            return {"error": "no airborne data in window"}
        t = tel.t_s.to_numpy()

        def first_breach(mask, min_s=3.0):
            m = np.asarray(mask, bool)
            run = 0
            for i, v in enumerate(m):
                run = run + 1 if v else 0
                if run >= min_s * 10:
                    return _r(t[i - run + 1], 1)
            return None

        if relation == "airspeed_vs_groundspeed":
            res = (tel.airspeed_mps - tel.groundspeed_mps).rolling(30, min_periods=5).median()
            breach = res.abs() > 15
            return {"relation": relation, "residual_median": _r(res.median()), "residual_min": _r(res.min()),
                    "residual_max": _r(res.max()), "breach_start_t": first_breach(breach),
                    "verdict": "INCONSISTENT" if breach.sum() > 30 else "consistent",
                    "rule": "|airspeed - groundspeed| should stay within ~15 m/s (wind)"}
        if relation == "elevator_cmd_vs_pos":
            err = (tel.elevator_cmd_deg - tel.elevator_pos_deg).abs()
            breach = err > 4
            return {"relation": relation, "rmse_deg": _r(np.sqrt((err**2).mean())), "max_err_deg": _r(err.max()),
                    "pos_std_deg": _r(tel.elevator_pos_deg.std()), "breach_start_t": first_breach(breach),
                    "verdict": "INCONSISTENT" if breach.sum() > 30 else "consistent",
                    "rule": "elevator position should track command within ~0.5 deg RMS"}
        if relation == "throttle_vs_rpm":
            out = {"relation": relation, "rule": "each motor rpm ~ 900 + 5200*throttle when healthy"}
            exp = 900 + 5200 * tel.throttle
            worst = None
            for i in range(1, 5):
                ratio = (tel[f"motor{i}_rpm"] / exp).rolling(20, min_periods=5).median()
                out[f"motor{i}_ratio_min"] = _r(ratio.min())
                b = first_breach(ratio < 0.5)
                if b is not None and (worst is None or b < worst[1]):
                    worst = (i, b)
            out["breach_motor"], out["breach_start_t"] = (worst if worst else (None, None))
            out["verdict"] = "INCONSISTENT" if worst else "consistent"
            return out
        if relation == "power_balance":
            batt_kw = tel.batt_v * tel.batt_a / 1000
            cruise = tel.phase_est.isin(["climb", "cruise"])
            gen_off = (tel.gen_kw < 1) & cruise & (tel.throttle > 0.15)
            res = {"relation": relation, "gen_kw_mean_climb_cruise": _r(tel.gen_kw[cruise].mean()),
                   "batt_kw_mean_climb_cruise": _r(batt_kw[cruise].mean()), "batt_v_min": _r(tel.batt_v.min()),
                   "batt_temp_max": _r(tel.batt_temp_c.max()), "gen_off_start_t": first_breach(gen_off, 5),
                   "rule": "generator should follow load in climb/cruise; batt_v should match fleet for same SOC"}
            res["verdict"] = "INCONSISTENT" if res["gen_off_start_t"] is not None else "consistent"
            return res
        if relation == "gnss_health_vs_innovation":
            healthy = (tel.gnss_nsat >= 8) & (tel.gnss_hdop < 2)
            innov_high = tel.ekf_pos_innov_m > 10
            lost = tel.gnss_nsat < 4
            return {"relation": relation, "nsat_min": _r(tel.gnss_nsat.min()), "hdop_max": _r(tel.gnss_hdop.max()),
                    "pos_innov_max": _r(tel.ekf_pos_innov_m.max()), "hacc_max": _r(tel.ekf_hacc_m.max()),
                    "innov_high_while_healthy_start_t": first_breach(healthy & innov_high),
                    "satellites_lost_start_t": first_breach(lost, 1),
                    "rule": "large innovations with healthy satellites => spoofing; satellites lost => jamming"}
        return {"error": f"unknown relation {relation}"}

    def compare_to_fleet(self, flight_id: str, metric: str) -> dict:
        """Percentile of a flight metric within the fleet."""
        cat = self.lake.catalog()
        if metric not in cat:
            return {"error": f"unknown metric; options: {sorted(c for c in cat.columns if cat[c].dtype.kind == 'f')}"}
        v = cat.loc[cat.flight_id == flight_id, metric]
        if v.empty or pd.isna(v.iloc[0]):
            return {"error": "metric not available for this flight"}
        col = cat[metric].dropna()
        return {"metric": metric, "value": _r(v.iloc[0]), "fleet_median": _r(col.median()),
                "fleet_p05": _r(col.quantile(0.05)), "fleet_p95": _r(col.quantile(0.95)),
                "percentile": _r(100 * (col < v.iloc[0]).mean(), 1)}

    def search_knowledge_base(self, query: str, k: int = 3) -> dict:
        """BM25 search over FMEA, troubleshooting guides and test procedures."""
        return {"results": self.kb.search(query, k)}

    def find_similar_incidents(self, flight_id: str, k: int = 3) -> dict:
        """Past investigated incidents whose deviation signature is most similar to this flight."""
        p = Path(self.incidents_path)
        if not p.exists():
            return {"error": "incident database not built (run `investigator build-incidents`)"}
        db = json.loads(p.read_text())
        q = signature(scan(self.lake.telemetry(flight_id), self._fleet_baseline(), 4.0, top=25))
        sims = []
        for inc in db["incidents"]:
            if inc["flight_id"] == flight_id:
                continue
            a, b = np.array(q), np.array(inc["signature"])
            s = float(a @ b / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
            sims.append((s, inc))
        sims.sort(key=lambda x: -x[0])
        return {"incidents": [{"flight_id": i["flight_id"], "similarity": _r(s, 3), "root_cause": i["root_cause"],
                               "resolution": i["resolution"]} for s, i in sims[:k]]}

    def run_sql(self, query: str) -> dict:
        """Read-only SQL (DuckDB). Tables: flights, telemetry, messages. Max 200 rows."""
        if re.search(r"\b(labels|injected_faults|fault_onset_s)\b", query, re.I):
            return {"error": "ground-truth tables/columns are not accessible"}
        try:
            df = self.lake.sql(guard(query, 200))
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"[:400]}
        return {"rows": json.loads(df.round(3).to_json(orient="records"))}


# ---------------------------------------------------------------- fleet envelope & signatures
def fit_baseline(lake: Lake, flight_ids: list[str]) -> dict:
    df = pd.concat([lake.telemetry(f, DEV_CHANNELS) for f in flight_ids])
    out = {}
    for ph, g in df.groupby("phase_est"):
        med = g[DEV_CHANNELS].median()
        mad = (g[DEV_CHANNELS] - med).abs().median() * 1.4826
        p99 = (g[DEV_CHANNELS] - med).abs().quantile(0.99)
        out[ph] = {"median": med.to_dict(), "scale": np.maximum(mad, p99 / 6).clip(lower=1e-3).to_dict()}
    return out


def scan(tel: pd.DataFrame, baseline: dict, z_thr: float = 4.0, min_s: float = 3.0, top: int = 10) -> list[dict]:
    sm = tel[DEV_CHANNELS].rolling(30, center=True, min_periods=5).median()
    n = len(tel)
    med, sc = np.full((n, len(DEV_CHANNELS)), np.nan), np.full((n, len(DEV_CHANNELS)), np.nan)
    ph = tel.phase_est.to_numpy()
    for p, b in baseline.items():
        if p in AIR:
            idx = ph == p
            med[idx] = [b["median"][c] for c in DEV_CHANNELS]
            sc[idx] = [b["scale"][c] for c in DEV_CHANNELS]
    Z = (sm.to_numpy() - med) / sc
    out = []
    t = tel.t_s.to_numpy()
    for j, c in enumerate(DEV_CHANNELS):
        hit = np.abs(np.nan_to_num(Z[:, j])) >= z_thr
        if not hit.any():
            continue
        starts = np.flatnonzero(np.r_[hit[0], hit[1:] & ~hit[:-1]])
        ends = np.flatnonzero(np.r_[hit[:-1] & ~hit[1:], hit[-1]])
        for s, e in zip(starts, ends):
            if (e - s + 1) < min_s * 10:
                continue
            k = s + int(np.nanargmax(np.abs(Z[s:e + 1, j])))
            out.append({"channel": c, "t_start": _r(t[s], 1), "t_end": _r(t[e], 1), "phase": str(ph[s]),
                        "peak_z": _r(Z[k, j], 1), "value": _r(sm.iloc[k, j]), "normal": _r(med[k, j])})
    out.sort(key=lambda d: -abs(d["peak_z"]) * min(d["t_end"] - d["t_start"] + 1, 120) ** 0.5)
    return sorted(out[:top], key=lambda d: d["t_start"])


def signature(devs: list[dict]) -> list[float]:
    """Fixed-length deviation signature: per channel, signed log-magnitude of the strongest excursion."""
    v = np.zeros(len(DEV_CHANNELS))
    for d in devs:
        i = DEV_CHANNELS.index(d["channel"])
        z = float(d["peak_z"])
        if abs(z) > abs(v[i]):
            v[i] = np.sign(z) * np.log1p(abs(z))
    return v.round(4).tolist()


# ---------------------------------------------------------------- tiny BM25 knowledge base
class KnowledgeBase:
    def __init__(self, kb_dir: str):
        self.chunks = []
        for f in sorted(Path(kb_dir).glob("*.md")):
            title = f.stem
            for sec in re.split(r"\n(?=## )", f.read_text()):
                head = sec.strip().splitlines()[0].lstrip("# ").strip() if sec.strip() else ""
                self.chunks.append({"doc": title, "section": head, "text": sec.strip()})
        self.toks = [self._tok(c["section"] + " " + c["text"]) for c in self.chunks]
        self.avgdl = np.mean([len(t) for t in self.toks]) if self.toks else 1
        df: dict[str, int] = {}
        for t in self.toks:
            for w in set(t):
                df[w] = df.get(w, 0) + 1
        N = len(self.toks)
        self.idf = {w: math.log(1 + (N - n + 0.5) / (n + 0.5)) for w, n in df.items()}

    @staticmethod
    @lru_cache(maxsize=4096)
    def _tok_cached(s: str) -> tuple:
        return tuple(re.findall(r"[a-z0-9_]+", s.lower()))

    def _tok(self, s: str) -> list[str]:
        return list(self._tok_cached(s))

    def search(self, query: str, k: int = 3, k1: float = 1.5, b: float = 0.75) -> list[dict]:
        q = self._tok(query)
        scores = []
        for i, t in enumerate(self.toks):
            tf: dict[str, int] = {}
            for w in t:
                tf[w] = tf.get(w, 0) + 1
            s = sum(self.idf.get(w, 0) * tf.get(w, 0) * (k1 + 1) /
                    (tf.get(w, 0) + k1 * (1 - b + b * len(t) / self.avgdl)) for w in q)
            scores.append(s)
        idx = np.argsort(scores)[::-1][:k]
        return [{"doc": self.chunks[i]["doc"], "section": self.chunks[i]["section"],
                 "text": self.chunks[i]["text"][:900], "score": _r(scores[i], 2)} for i in idx if scores[i] > 0]


TOOL_NAMES = ["list_flights", "get_flight_overview", "get_messages", "get_channel_stats", "get_timeseries",
              "scan_deviations", "check_relation", "compare_to_fleet", "search_knowledge_base",
              "find_similar_incidents", "run_sql"]
