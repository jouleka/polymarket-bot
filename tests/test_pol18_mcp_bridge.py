import asyncio
import ast
import os
from pathlib import Path
import sys

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer


APPROVED = {
    "propose_trade", "get_market", "get_book", "get_news", "get_ledger", "get_flags",
}


class _Client:
    def __init__(self):
        self.calls = []

    async def call(self, method, params):
        self.calls.append((method, params))
        return {"method": method, "params": params}


def test_mcp_discovery_is_exactly_six_strict_tools_and_no_other_capability():
    from polybot.hermes.mcp_bridge import ProposalMcpServer

    bridge = ProposalMcpServer(_Client())
    definitions = bridge.tool_definitions

    assert {tool.name for tool in definitions} == APPROVED
    assert len(definitions) == len(APPROVED)
    assert all(tool.inputSchema["additionalProperties"] is False for tool in definitions)
    by_name = {tool.name: tool for tool in definitions}
    proposal = by_name["propose_trade"].inputSchema
    assert proposal["properties"]["target_price"] == {"type": "string"}
    assert proposal["properties"]["size_usd_suggestion"] == {"type": "string"}
    assert proposal["properties"]["citations"]["items"] == {"type": "string"}
    assert by_name["get_news"].inputSchema["properties"]["limit"] == {
        "type": "integer", "minimum": 1, "maximum": 50,
    }
    assert by_name["get_news"].inputSchema["properties"]["offset"] == {
        "type": "integer", "minimum": 0, "maximum": 1000,
    }
    assert by_name["get_news"].inputSchema["properties"]["query"] == {
        "type": "string", "minLength": 1, "maxLength": 128,
    }
    assert bridge.capabilities.resources is None
    assert bridge.capabilities.prompts is None
    assert bridge.capabilities.tools is not None


def test_mcp_tool_call_maps_one_to_one_to_the_socket_client():
    from polybot.hermes.mcp_bridge import ProposalMcpServer

    client = _Client()
    bridge = ProposalMcpServer(client)

    result = asyncio.run(bridge.call_tool("get_book", {"token_id": "11"}))

    assert result == {"method": "get_book", "params": {"token_id": "11"}}
    assert client.calls == [("get_book", {"token_id": "11"})]


def test_mcp_bridge_imports_only_sdk_and_socket_client_capabilities():
    path = Path(__file__).resolve().parents[1] / "src" / "polybot" / "hermes" / "mcp_bridge.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")

    forbidden = {
        "intent_store", "controller", "signer", "wallet", "order", "cancel",
        "redeem", "settlement", "subprocess", "sqlite", "terminal", "shell",
    }
    assert not {
        fragment for fragment in forbidden
        if any(fragment in module.lower() for module in imported)
    }
    assert set(imported) <= {
        "__future__", "argparse", "asyncio", "mcp", "mcp.server.lowlevel",
        "mcp.server.stdio", "polybot.hermes.rpc",
    }


def test_real_stdio_mcp_discovers_six_and_calls_the_unix_boundary(tmp_path):
    class Facade:
        def get_book(self, *, token_id):
            return {"token_id": token_id, "midpoint": "0.42"}

    async def scenario():
        socket_path = tmp_path / "mcp.sock"
        rpc = ProposalRpcServer(
            socket_path, ProposalRpcDispatcher(Facade()), runtime_ready=lambda: True,
        )
        rpc_task = asyncio.create_task(rpc.run())
        try:
            await asyncio.wait_for(rpc.started.wait(), timeout=1)
            params = StdioServerParameters(
                command=sys.executable,
                args=[
                    "-m", "polybot.hermes.mcp_bridge", "--socket", str(socket_path),
                ],
                env=os.environ | {
                    "PYTHONPATH": str(Path(__file__).resolve().parents[1] / "src"),
                },
            )
            async with stdio_client(params) as (read_stream, write_stream):
                async with ClientSession(read_stream, write_stream) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {tool.name for tool in tools.tools} == APPROVED
                    result = await session.call_tool("get_book", {"token_id": "11"})
                    assert result.isError is False
                    assert result.structuredContent == {
                        "token_id": "11", "midpoint": "0.42",
                    }
        finally:
            rpc_task.cancel()
            try:
                await rpc_task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
