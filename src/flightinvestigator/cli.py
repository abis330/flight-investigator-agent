"""``investigator`` command line."""
from __future__ import annotations

import json
import os

import typer

app = typer.Typer(add_completion=False, help="Multi-agent flight-log investigator")


def _toolbox(lake: str, incidents: str, mcp: bool):
    os.environ.update(FLIGHTLAKE_ROOT=lake, INCIDENTS_PATH=incidents)
    if mcp:
        from .toolbox import MCPToolBox

        return MCPToolBox()
    from .toolbox import InProcessToolBox
    from .tools import FlightTools

    return InProcessToolBox(FlightTools(lake, incidents_path=incidents))


@app.command("build-incidents")
def build_incidents(lake: str = "data/lake", out: str = "data/incidents.json"):
    """Build the similar-incident DB + fleet envelope from the history split."""
    from .incidents import build

    typer.echo(json.dumps(build(lake, out)))


@app.command()
def investigate(flight_id: str, lake: str = "data/lake", incidents: str = "data/incidents.json",
                provider: str = typer.Option("scripted", help="scripted | anthropic | openai"),
                model: str = None, mcp: bool = typer.Option(False, help="call tools through the MCP server"),
                markdown: bool = True):
    """Investigate one flight and print the report."""
    from .agents import investigate as _inv
    from .agents import make_llm, to_markdown

    tb = _toolbox(lake, incidents, mcp)
    rep = _inv(flight_id, make_llm(provider, model), tb)
    typer.echo(to_markdown(rep) if markdown else json.dumps(rep, indent=2, default=str))
    if mcp:
        tb.close()


@app.command()
def evaluate(lake: str = "data/lake", incidents: str = "data/incidents.json", provider: str = "scripted",
             model: str = None, limit: int = 0, mcp: bool = False, out: str = "results"):
    """Run the golden-incident evaluation."""
    from .agents import make_llm
    from .evaluate import evaluate as _ev

    tb = _toolbox(lake, incidents, mcp)
    r = _ev(lake, make_llm(provider, model), tb, incidents, limit, out, name=f"{provider}{'-' + model if model else ''}")
    typer.echo(json.dumps(r, indent=2))


@app.command()
def serve(lake: str = "data/lake", incidents: str = "data/incidents.json", port: int = 8090):
    """REST API: GET /investigate/{flight_id}?provider=..."""
    import uvicorn

    os.environ.update(FLIGHTLAKE_ROOT=lake, INCIDENTS_PATH=incidents)
    uvicorn.run("flightinvestigator.api:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    app()
