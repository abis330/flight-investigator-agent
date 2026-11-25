"""Provider-agnostic tool-calling LLM clients.

Transcript format (neutral):
  {"role": "user", "text": str}
  {"role": "assistant", "text": str, "tool_calls": [{"id", "name", "args"}]}
  {"role": "tool", "id": str, "name": str, "content": str}
Each provider converts this to its native API. Usage (tokens) is returned for cost tracking.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field


@dataclass
class LLMResponse:
    text: str
    tool_calls: list[dict] = field(default_factory=list)
    usage: dict = field(default_factory=lambda: {"input_tokens": 0, "output_tokens": 0})


class AnthropicLLM:
    def __init__(self, model: str | None = None, temperature: float = 0.0, max_tokens: int = 2000):
        import anthropic

        self.client = anthropic.Anthropic()
        self.model = model or os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-5")
        self.temperature, self.max_tokens = temperature, max_tokens

    def complete(self, system: str, transcript: list[dict], tools: list[dict]) -> LLMResponse:
        msgs = []
        for ev in transcript:
            if ev["role"] == "user":
                msgs.append({"role": "user", "content": ev["text"]})
            elif ev["role"] == "assistant":
                content = ([{"type": "text", "text": ev["text"]}] if ev.get("text") else []) + [
                    {"type": "tool_use", "id": c["id"], "name": c["name"], "input": c["args"]} for c in ev["tool_calls"]]
                msgs.append({"role": "assistant", "content": content})
            else:
                block = {"type": "tool_result", "tool_use_id": ev["id"], "content": ev["content"]}
                if msgs and msgs[-1]["role"] == "user" and isinstance(msgs[-1]["content"], list):
                    msgs[-1]["content"].append(block)
                else:
                    msgs.append({"role": "user", "content": [block]})
        r = self.client.messages.create(
            model=self.model, system=system, messages=msgs, max_tokens=self.max_tokens, temperature=self.temperature,
            tools=[{"name": t["name"], "description": t["description"], "input_schema": t["parameters"]} for t in tools])
        text = "".join(b.text for b in r.content if b.type == "text")
        calls = [{"id": b.id, "name": b.name, "args": b.input} for b in r.content if b.type == "tool_use"]
        return LLMResponse(text, calls, {"input_tokens": r.usage.input_tokens, "output_tokens": r.usage.output_tokens})


class OpenAICompatLLM:
    """Any OpenAI-compatible endpoint: OpenAI, vLLM, Ollama, Gemini (OpenAI-compat), Together, ..."""

    def __init__(self, model: str | None = None, base_url: str | None = None, api_key: str | None = None,
                 temperature: float = 0.0):
        import httpx

        self.model = model or os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        self.url = (base_url or os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")).rstrip("/")
        self.key = api_key or os.environ.get("OPENAI_API_KEY", "EMPTY")
        self.http, self.temperature = httpx.Client(timeout=180), temperature

    def complete(self, system: str, transcript: list[dict], tools: list[dict]) -> LLMResponse:
        msgs = [{"role": "system", "content": system}]
        for ev in transcript:
            if ev["role"] == "user":
                msgs.append({"role": "user", "content": ev["text"]})
            elif ev["role"] == "assistant":
                m = {"role": "assistant", "content": ev.get("text") or None}
                if ev["tool_calls"]:
                    m["tool_calls"] = [{"id": c["id"], "type": "function", "function": {
                        "name": c["name"], "arguments": json.dumps(c["args"])}} for c in ev["tool_calls"]]
                msgs.append(m)
            else:
                msgs.append({"role": "tool", "tool_call_id": ev["id"], "content": ev["content"]})
        r = self.http.post(f"{self.url}/chat/completions", headers={"Authorization": f"Bearer {self.key}"}, json={
            "model": self.model, "messages": msgs, "temperature": self.temperature,
            "tools": [{"type": "function", "function": t} for t in tools]})
        r.raise_for_status()
        d = r.json()
        msg = d["choices"][0]["message"]
        calls = [{"id": c["id"], "name": c["function"]["name"], "args": json.loads(c["function"]["arguments"] or "{}")}
                 for c in msg.get("tool_calls") or []]
        u = d.get("usage", {})
        return LLMResponse(msg.get("content") or "", calls,
                           {"input_tokens": u.get("prompt_tokens", 0), "output_tokens": u.get("completion_tokens", 0)})


def make_llm(provider: str, model: str | None = None):
    if provider == "anthropic":
        return AnthropicLLM(model)
    if provider == "openai":
        return OpenAICompatLLM(model)
    if provider == "scripted":
        from .scripted import ScriptedLLM

        return ScriptedLLM()
    raise ValueError(provider)
