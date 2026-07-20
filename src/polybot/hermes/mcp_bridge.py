"""Exact-six-tool stdio MCP bridge to POL-17's Unix proposal endpoint."""

from __future__ import annotations

import argparse
import asyncio

from mcp import types
from mcp.server.lowlevel import NotificationOptions, Server
from mcp.server.stdio import stdio_server

from polybot.hermes.rpc import APPROVED_METHODS, ProposalRpcClient


def _object(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_STRING = {"type": "string"}
_POSITIVE_INTEGER = {"type": "integer", "minimum": 1}

TOOL_DEFINITIONS = (
    types.Tool(
        name="propose_trade",
        description=(
            "Enqueue one untrusted PROPOSED paper intent. This never validates, sizes, "
            "prices, signs, submits, cancels, settles, or authorizes a trade."
        ),
        inputSchema=_object(
            {
                "intent_id": _STRING,
                "token_id": _STRING,
                "condition_id": _STRING,
                "event_id": _STRING,
                "side": {"type": "string", "enum": ["BUY"]},
                "target_price": _STRING,
                "max_price": _STRING,
                "size_usd_suggestion": _STRING,
                "p": _STRING,
                "p_confidence": _STRING,
                "resolution_summary": _STRING,
                "thesis": _STRING,
                "citations": {
                    "type": "array", "items": _STRING, "maxItems": 32,
                },
            },
            required=(
                "intent_id", "token_id", "condition_id", "event_id", "side",
                "target_price", "max_price", "size_usd_suggestion", "p",
                "p_confidence",
            ),
        ),
    ),
    types.Tool(
        name="get_market",
        description="Read a bounded sanitized page or exact current registry market.",
        inputSchema=_object({
            "condition_id": _STRING,
            "token_id": _STRING,
            "offset": {"type": "integer", "minimum": 0},
            "limit": _POSITIVE_INTEGER,
        }),
    ),
    types.Tool(
        name="get_book",
        description="Read one current shared live book; stale books fail closed.",
        inputSchema=_object({"token_id": _STRING}, required=("token_id",)),
    ),
    types.Tool(
        name="get_news",
        description=(
            "Read bounded sanitized untrusted evidence from configured ingestion sources. "
            "Only citation_eligible IDs may be proposed as citations."
        ),
        inputSchema=_object({
            "offset": {"type": "integer", "minimum": 0},
            "limit": {"type": "integer", "minimum": 1, "maximum": 50},
        }),
    ),
    types.Tool(
        name="get_ledger",
        description="Read bounded resolved forecast and terminal outcome history only.",
        inputSchema=_object({"category": _STRING, "limit": _POSITIVE_INTEGER}),
    ),
    types.Tool(
        name="get_flags",
        description=(
            "Read conservative runtime, resolution, book, and detector availability facts; "
            "the result is never permission to trade."
        ),
        inputSchema=_object({}),
    ),
)


class ProposalMcpServer:
    """Low-level MCP server with no prompt, resource, or generic tool surface."""

    def __init__(self, client):
        if not callable(getattr(client, "call", None)):
            raise TypeError("MCP bridge client must expose async call(method, params)")
        self._client = client
        self._server = Server(
            "polybot-propose-only", version="1",
            instructions="Hermes proposes; the deterministic POL-17 ERS disposes.",
        )

        @self._server.list_tools()
        async def list_tools():
            return list(TOOL_DEFINITIONS)

        @self._server.call_tool(validate_input=True)
        async def call_tool(name, arguments):
            result = await self.call_tool(name, arguments)
            return result if isinstance(result, dict) else {"result": result}

    @property
    def tool_definitions(self):
        return TOOL_DEFINITIONS

    @property
    def capabilities(self):
        return self._server.get_capabilities(
            NotificationOptions(), experimental_capabilities={},
        )

    async def call_tool(self, name, arguments):
        if name not in APPROVED_METHODS:
            raise ValueError("MCP tool is not approved")
        if not isinstance(arguments, dict):
            raise TypeError("MCP tool arguments must be an object")
        return await self._client.call(name, arguments)

    async def run_stdio(self):
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(
                read_stream,
                write_stream,
                self._server.create_initialization_options(NotificationOptions(), {}),
            )


def main(argv=None):
    parser = argparse.ArgumentParser(prog="polybot-hermes-mcp")
    parser.add_argument("--socket", required=True, help="absolute POL-17 proposal socket")
    args = parser.parse_args(argv)
    bridge = ProposalMcpServer(ProposalRpcClient(args.socket))
    asyncio.run(bridge.run_stdio())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
