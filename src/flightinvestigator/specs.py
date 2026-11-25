"""JSON-schema tool specs shown to LLMs (shared by in-process and MCP modes)."""
from __future__ import annotations

S, N, INT = {"type": "string"}, {"type": "number"}, {"type": "integer"}
FID = {"flight_id": {"type": "string", "description": "flight id, e.g. FLT-00012"}}
CHANNELS = {"type": "array", "items": {"type": "string"}, "description":
            "canonical channels e.g. airspeed_mps, groundspeed_mps, throttle, motor1_rpm..motor4_rpm, batt_v, batt_a, "
            "batt_temp_c, gen_kw, gnss_nsat, gnss_hdop, ekf_pos_innov_m, ekf_hacc_m, vibe_mps2, elevator_cmd_deg, "
            "elevator_pos_deg, pitch_deg, roll_deg, vz_mps, alt_rel_m"}

SPECS = {
    "list_flights": ("List flights in the fleet catalog.", {"site": S, "min_qc": N, "limit": INT}, []),
    "get_flight_overview": ("Metadata, phase timeline, key metrics and data-quality summary of a flight.", FID,
                            ["flight_id"]),
    "get_messages": ("Autopilot text messages at or above a severity level, optionally in a time window.",
                     {**FID, "min_level": {"type": "string", "enum": ["INFO", "WARNING", "ERROR"]}, "t0": N, "t1": N},
                     ["flight_id"]),
    "get_channel_stats": ("min/mean/max/std/first/last of channels in a time window (seconds).",
                          {**FID, "channels": CHANNELS, "t0": N, "t1": N}, ["flight_id", "channels"]),
    "get_timeseries": ("Downsampled time series (<=40 points) of up to 4 channels between t0 and t1.",
                       {**FID, "channels": CHANNELS, "t0": N, "t1": N, "max_points": INT},
                       ["flight_id", "channels", "t0", "t1"]),
    "scan_deviations": ("Fault-agnostic scan for channel segments outside the fleet-normal envelope of the same phase.",
                        {**FID, "z_threshold": N, "top": INT}, ["flight_id"]),
    "check_relation": ("Physics-consistency check between related signals; returns verdict and first breach time.",
                       {**FID, "relation": {"type": "string", "enum": [
                           "airspeed_vs_groundspeed", "elevator_cmd_vs_pos", "throttle_vs_rpm", "power_balance",
                           "gnss_health_vs_innovation"]}, "t0": N, "t1": N}, ["flight_id", "relation"]),
    "compare_to_fleet": ("Percentile of a flight-level metric (e.g. min_batt_v, max_vibe_mps2, max_batt_temp_c, "
                         "gnss_outage_s, takeoff_ground_roll_m) within the fleet.",
                         {**FID, "metric": S}, ["flight_id", "metric"]),
    "search_knowledge_base": ("Search FMEA, troubleshooting guides and flight-test procedures.",
                              {"query": S, "k": INT}, ["query"]),
    "find_similar_incidents": ("Past investigated incidents with the most similar deviation signature.",
                               {**FID, "k": INT}, ["flight_id"]),
    "run_sql": ("Read-only DuckDB SQL over tables flights, telemetry, messages (max 200 rows).", {"query": S},
                ["query"]),
}


def tool_specs(names: list[str]) -> list[dict]:
    return [{"name": n, "description": SPECS[n][0],
             "parameters": {"type": "object", "properties": SPECS[n][1], "required": SPECS[n][2]}} for n in names]
