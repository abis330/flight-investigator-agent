"""MCP server exposing the read-only flight diagnostic tools.

stdio (Claude Desktop / Claude Code / any MCP client):
    python -m flightinvestigator.mcp_server
Streamable HTTP (remote, behind auth proxy):
    python -m flightinvestigator.mcp_server --http --port 8765

Claude Desktop config (claude_desktop_config.json):
    {"mcpServers": {"flight-tools": {"command": "python", "args": ["-m", "flightinvestigator.mcp_server"],
                                     "env": {"FLIGHTLAKE_ROOT": "/abs/path/data/lake",
                                             "INCIDENTS_PATH": "/abs/path/data/incidents.json"}}}}
"""
from __future__ import annotations

import argparse

from mcp.types import ToolAnnotations

from .tools import FlightTools

try:  # MCP Python SDK >= 2.0
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # MCP Python SDK 1.x
    from mcp.server.fastmcp import FastMCP as _Server

mcp = _Server("flight-tools", instructions="Read-only diagnostic tools over flight-test telemetry of a "
              "hybrid-electric STOL aircraft. Ground-truth labels are never exposed.")
RO = ToolAnnotations(readOnlyHint=True, openWorldHint=False)
_tools: FlightTools | None = None


def T() -> FlightTools:
    global _tools
    if _tools is None:
        _tools = FlightTools()
    return _tools


@mcp.tool(annotations=RO)
def list_flights(site: str | None = None, min_qc: float = 0.0, limit: int = 20) -> dict:
    """List flights in the fleet catalog."""
    return T().list_flights(site, min_qc, limit)


@mcp.tool(annotations=RO)
def get_flight_overview(flight_id: str) -> dict:
    """Metadata, phase timeline, key metrics and data-quality summary of a flight."""
    return T().get_flight_overview(flight_id)


@mcp.tool(annotations=RO)
def get_messages(flight_id: str, min_level: str = "WARNING", t0: float | None = None, t1: float | None = None) -> dict:
    """Autopilot text messages at or above a severity level (INFO|WARNING|ERROR)."""
    return T().get_messages(flight_id, min_level, t0, t1)


@mcp.tool(annotations=RO)
def get_channel_stats(flight_id: str, channels: list[str], t0: float | None = None, t1: float | None = None) -> dict:
    """min/mean/max/std/first/last of telemetry channels in a time window (seconds)."""
    return T().get_channel_stats(flight_id, channels, t0, t1)


@mcp.tool(annotations=RO)
def get_timeseries(flight_id: str, channels: list[str], t0: float, t1: float, max_points: int = 40) -> dict:
    """Downsampled time series of up to 4 channels."""
    return T().get_timeseries(flight_id, channels, t0, t1, max_points)


@mcp.tool(annotations=RO)
def scan_deviations(flight_id: str, z_threshold: float = 4.0, top: int = 10) -> dict:
    """Channel segments outside the fleet-normal envelope for the same flight phase."""
    return T().scan_deviations(flight_id, z_threshold, top)


@mcp.tool(annotations=RO)
def check_relation(flight_id: str, relation: str, t0: float | None = None, t1: float | None = None) -> dict:
    """Physics consistency: airspeed_vs_groundspeed | elevator_cmd_vs_pos | throttle_vs_rpm | power_balance |
    gnss_health_vs_innovation."""
    return T().check_relation(flight_id, relation, t0, t1)


@mcp.tool(annotations=RO)
def compare_to_fleet(flight_id: str, metric: str) -> dict:
    """Percentile of a flight-level metric within the fleet."""
    return T().compare_to_fleet(flight_id, metric)


@mcp.tool(annotations=RO)
def search_knowledge_base(query: str, k: int = 3) -> dict:
    """BM25 search over FMEA, troubleshooting guides and flight-test procedures."""
    return T().search_knowledge_base(query, k)


@mcp.tool(annotations=RO)
def find_similar_incidents(flight_id: str, k: int = 3) -> dict:
    """Past investigated incidents with the most similar deviation signature."""
    return T().find_similar_incidents(flight_id, k)


@mcp.tool(annotations=RO)
def run_sql(query: str) -> dict:
    """Read-only DuckDB SQL over tables flights, telemetry, messages (max 200 rows)."""
    return T().run_sql(query)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--port", type=int, default=8765)
    a = ap.parse_args()
    if a.http:
        try:
            mcp.run(transport="streamable-http", host="0.0.0.0", port=a.port)  # SDK 2.x
        except TypeError:
            mcp.settings.host, mcp.settings.port = "0.0.0.0", a.port  # SDK 1.x
            mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
