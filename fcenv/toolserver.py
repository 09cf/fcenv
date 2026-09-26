"""A session's tools served over MCP, for agents that speak it (e.g. Claude
Code, driven by cc.py).

    with ToolServer(session, on_call=...) as srv:
        ...  # point the agent at srv.url

The server runs in a background thread on localhost (streamable HTTP), so
the session lives in the caller's process, across however many agent runs
connect to it. Tools are the same as tools.tools_for(session), with the
same results; calls run one at a time, and on_call() runs after each (e.g.
to record the session).
"""

import socket
import threading
import time

import anyio
import mcp.types as t
import uvicorn
from mcp.server.lowlevel import Server

from . import tools


class ToolServer:
    def __init__(self, session, on_call=None, name="fc"):
        self.session = session
        self.on_call = on_call
        self.name = name
        self.url = None
        self._lock = threading.Lock()

        async def list_tools(ctx, params):
            return t.ListToolsResult(tools=[
                t.Tool(name=x["name"], description=x["description"],
                       input_schema=x["input_schema"])
                for x in tools.tools_for(session)])

        async def call_tool(ctx, params):
            return await anyio.to_thread.run_sync(self._call, params.name,
                                                  dict(params.arguments or {}))

        self._server = Server(name, on_list_tools=list_tools, on_call_tool=call_tool)

    def _call(self, name, args):
        with self._lock:
            ok, blocks = tools.dispatch(self.session, name, args)
            if self.on_call:
                self.on_call()
        content = [t.TextContent(type="text", text=b["text"]) if b["type"] == "text"
                   else t.ImageContent(type="image", data=b["source"]["data"],
                                       mime_type=b["source"]["media_type"])
                   for b in blocks]
        return t.CallToolResult(content=content, is_error=not ok)

    def tool_names(self):
        """The tools' names as an MCP client that prefixes them sees them
        (Claude Code: mcp__<server>__<tool>)."""
        return [f"mcp__{self.name}__{x['name']}" for x in tools.tools_for(self.session)]

    def __enter__(self):
        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        app = self._server.streamable_http_app(host="127.0.0.1")
        self._uv = uvicorn.Server(uvicorn.Config(app, log_level="warning"))
        self._thread = threading.Thread(target=self._uv.run, kwargs={"sockets": [sock]},
                                        daemon=True)
        self._thread.start()
        deadline = time.time() + 10
        while not self._uv.started:
            if time.time() > deadline or not self._thread.is_alive():
                raise RuntimeError("the MCP tool server didn't start")
            time.sleep(0.02)
        self.url = f"http://127.0.0.1:{port}/mcp"
        return self

    def __exit__(self, *exc):
        self._uv.should_exit = True
        self._thread.join(10)
