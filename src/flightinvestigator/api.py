"""REST API for on-demand investigations (the agent runs in-process with the tool layer)."""
from __future__ import annotations

import os
from functools import lru_cache

from fastapi import FastAPI, Query
from fastapi.responses import PlainTextResponse

from .agents import investigate, make_llm, to_markdown
from .toolbox import InProcessToolBox
from .tools import FlightTools

app = FastAPI(title="flight-investigator-agent")


@lru_cache(maxsize=1)
def toolbox():
    return InProcessToolBox(FlightTools(os.environ.get("FLIGHTLAKE_ROOT", "data/lake"),
                                       incidents_path=os.environ.get("INCIDENTS_PATH", "data/incidents.json")))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/investigate/{flight_id}")
def run(flight_id: str, provider: str = Query(os.environ.get("LLM_PROVIDER", "scripted")), model: str | None = None,
        format: str = "json"):
    rep = investigate(flight_id, make_llm(provider, model), toolbox())
    return PlainTextResponse(to_markdown(rep)) if format == "md" else rep
