import json
import os

import pytest

from flightinvestigator.agents import investigate, make_llm, to_markdown
from flightinvestigator.agents.graph import verify_claim, verify_finding
from flightinvestigator.toolbox import InProcessToolBox
from flightinvestigator.tools import FlightTools


def test_verifier_rejects_invented_numbers():
    src = [json.dumps({"max_err_deg": 12.31, "breach_start_t": 351.2})]
    assert verify_claim("tracking error 12.31 deg from t=351.2 s", src)[0]
    ok, missing = verify_claim("tracking error 19.5 deg from t=351.2 s", src)
    assert not ok and missing == ["19.5"]
    assert not verify_claim("the elevator looks stuck", src)[0]  # no numbers -> not evidence
    f = verify_finding({"fault_type": "elevator_stuck", "confidence": 0.9, "evidence": [
        {"claim": "error 19.5 deg", "tool_call_id": "c1"}]}, [{"id": "c1", "content": src[0]}])
    assert f["verified_score"] < 0.3


def test_tools_hide_ground_truth(env):
    t = FlightTools(env["lake"], incidents_path=env["incidents"])
    fl = t.list_flights(limit=50)
    assert "injected_faults" not in json.dumps(fl)
    assert "error" in t.run_sql("select * from labels")
    assert "error" in t.run_sql("select injected_faults from flights")
    assert t.run_sql("select count(*) as n from telemetry")["rows"][0]["n"] > 1000


def test_knowledge_base_search(env):
    t = FlightTools(env["lake"], incidents_path=env["incidents"])
    res = t.search_knowledge_base("satellites healthy but innovations growing spoofing")["results"]
    assert res and "spoof" in (res[0]["section"] + res[0]["text"]).lower()


def test_scripted_investigation_end_to_end(env):
    tools = FlightTools(env["lake"], incidents_path=env["incidents"])
    tb = InProcessToolBox(tools)
    golden = json.loads(open(env["incidents"]).read())["golden_flights"]
    fid = golden[0] if golden else tools.lake.flight_ids()[0]
    rep = investigate(fid, make_llm("scripted"), tb)
    assert rep["root_cause"] in {"none", "motor_failure", "gnss_jamming", "gnss_spoofing", "battery_degradation",
                                 "imu_vibration", "pitot_blockage", "elevator_stuck", "generator_dropout"}
    assert all(e["grounded"] for e in rep["evidence"])
    assert rep["trace"]["tool_calls"] >= 4
    assert "# Flight investigation" in to_markdown(rep)


@pytest.mark.timeout(120) if hasattr(pytest.mark, "timeout") else (lambda f: f)
def test_mcp_stdio_roundtrip(env):
    from flightinvestigator.toolbox import MCPToolBox

    tb = MCPToolBox(env={"FLIGHTLAKE_ROOT": env["lake"], "INCIDENTS_PATH": env["incidents"]})
    try:
        assert "check_relation" in tb.tool_names and len(tb.tool_names) == 11
        fid = FlightTools(env["lake"]).lake.flight_ids()[0]
        out = tb.call("get_flight_overview", {"flight_id": fid})
        assert out["flight_id"] == fid and out["phases"]
    finally:
        tb.close()
    os.environ.pop("FLIGHTLAKE_ROOT", None)
