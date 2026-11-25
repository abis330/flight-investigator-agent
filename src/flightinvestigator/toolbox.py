"""Tool execution back-ends: in-process (fast, tests) or a real MCP client session (stdio / HTTP)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import threading

from .tools import FlightTools


class InProcessToolBox:
    def __init__(self, tools: FlightTools):
        self.tools = tools

    def call(self, name: str, args: dict):
        return getattr(self.tools, name)(**(args or {}))


class MCPToolBox:
    """Talks to `flightinvestigator.mcp_server` over stdio using the official MCP Python SDK.

    The client session lives in one long-running asyncio task on a background thread (anyio
    cancel scopes must be entered and exited in the same task); the sync agent graph submits
    tool calls to that loop."""

    def __init__(self, env: dict | None = None, command: str = sys.executable, args: list[str] | None = None,
                 timeout: float = 120):
        from concurrent.futures import Future

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        self._loop = asyncio.new_event_loop()
        threading.Thread(target=self._loop.run_forever, daemon=True).start()
        params = StdioServerParameters(command=command, args=args or ["-m", "flightinvestigator.mcp_server"],
                                       env={**os.environ, **(env or {})})
        ready: Future = Future()

        async def _main():
            self._closed = asyncio.Event()
            try:
                async with stdio_client(params) as (read, write):
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        self.session = session
                        tools = await session.list_tools()
                        ready.set_result([t.name for t in tools.tools])
                        await self._closed.wait()
            except Exception as e:  # surface startup errors to the caller
                if not ready.done():
                    ready.set_exception(e)
                raise

        self._main = asyncio.run_coroutine_threadsafe(_main(), self._loop)
        self.tool_names = ready.result(timeout)
        self.timeout = timeout

    def call(self, name: str, args: dict):
        fut = asyncio.run_coroutine_threadsafe(self.session.call_tool(name, args or {}), self._loop)
        res = fut.result(self.timeout)
        if getattr(res, "structuredContent", None):
            sc = res.structuredContent
            return sc.get("result", sc) if isinstance(sc, dict) else sc
        text = "".join(getattr(c, "text", "") for c in res.content)
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return {"text": text}

    def close(self):
        self._loop.call_soon_threadsafe(self._closed.set)
        try:
            self._main.result(15)
        finally:
            self._loop.call_soon_threadsafe(self._loop.stop)
