import json
import asyncio
import socket
from decimal import Decimal


class _Facade:
    def __init__(self):
        self.calls = []

    def get_book(self, **params):
        self.calls.append(("get_book", params))
        return {"token_id": params["token_id"], "midpoint": "0.4200"}

    def propose_trade(self, *args, **params):
        self.calls.append(("propose_trade", args, params))
        return True


def _wire(payload):
    return json.dumps(payload, separators=(",", ":")).encode("utf-8") + b"\n"


def _proposal(request_id="proposal-request"):
    return _wire({
        "version": 1,
        "id": request_id,
        "method": "propose_trade",
        "params": {
            "intent_id": "intent-1",
            "token_id": "11",
            "condition_id": "0x" + "ab" * 32,
            "event_id": "7",
            "side": "BUY",
            "target_price": "0.42",
            "max_price": "0.45",
            "size_usd_suggestion": "5.00",
            "p": "0.60",
            "p_confidence": "0.80",
            "citations": ["https://example.test/evidence"],
        },
    })


def test_rpc_dispatches_one_approved_read_and_preserves_decimal_strings():
    from polybot.hermes.rpc import ProposalRpcDispatcher

    facade = _Facade()
    dispatcher = ProposalRpcDispatcher(facade)
    request = _wire({
        "version": 1,
        "id": "request-1",
        "method": "get_book",
        "params": {"token_id": "11"},
    })

    response = json.loads(dispatcher.handle(request))

    assert response == {
        "version": 1,
        "id": "request-1",
        "result": {"token_id": "11", "midpoint": "0.4200"},
    }
    assert facade.calls == [("get_book", {"token_id": "11"})]


def test_rpc_rejects_json_float_before_the_proposal_facade():
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, RpcProtocolError

    facade = _Facade()
    dispatcher = ProposalRpcDispatcher(facade)
    request = _wire({
        "version": 1,
        "id": "request-2",
        "method": "propose_trade",
        "params": {
            "intent_id": "intent-1",
            "token_id": "11",
            "condition_id": "0x" + "ab" * 32,
            "event_id": "7",
            "side": "BUY",
            "target_price": 0.42,
            "max_price": "0.45",
            "size_usd_suggestion": "5.00",
            "p": "0.60",
            "p_confidence": "0.80",
        },
    })

    with pytest.raises(RpcProtocolError, match="decimal string"):
        dispatcher.handle(request)
    assert facade.calls == []


def test_rpc_rate_gate_runs_immediately_before_insert_only_proposal():
    import pytest

    from polybot.hermes.rpc import (
        ProposalRateLimiter, ProposalRpcDispatcher, RpcProtocolError,
    )

    now = [10.0]
    facade = _Facade()
    dispatcher = ProposalRpcDispatcher(
        facade,
        proposal_gate=ProposalRateLimiter(1, 60.0, clock=lambda: now[0]),
    )

    assert json.loads(dispatcher.handle(_proposal("first")))["result"] is True
    with pytest.raises(RpcProtocolError, match="rate limit"):
        dispatcher.handle(_proposal("second"))
    name, args, params = facade.calls[0]
    assert name == "propose_trade" and args == ()
    assert params["target_price"] == Decimal("0.42")
    assert params["citations"] == ("https://example.test/evidence",)
    assert len(facade.calls) == 1


def test_unix_server_serves_one_bounded_request_and_cleans_up_socket(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "proposal.sock"
        facade = _Facade()
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(facade), runtime_ready=lambda: True,
        )
        task = asyncio.create_task(server.run())
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(_wire({
                "version": 1,
                "id": "request-3",
                "method": "get_book",
                "params": {"token_id": "11"},
            }))
            await writer.drain()
            response = json.loads(await asyncio.wait_for(reader.readline(), timeout=1))
            writer.close()
            await writer.wait_closed()
            assert response["result"]["midpoint"] == "0.4200"
            assert (path.stat().st_mode & 0o777) == 0o660
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        assert not path.exists()

    asyncio.run(scenario())


def test_unix_server_recovers_only_a_proven_stale_socket(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    path = tmp_path / "stale.sock"
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()

    async def scenario():
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )
        task = asyncio.create_task(server.run())
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            assert path.exists()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
    assert not path.exists()


def test_unix_client_round_trips_without_any_database_capability(tmp_path):
    from polybot.hermes.rpc import (
        ProposalRpcClient, ProposalRpcDispatcher, ProposalRpcServer,
    )

    async def scenario():
        path = tmp_path / "client.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )
        task = asyncio.create_task(server.run())
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            client = ProposalRpcClient(path, request_id=lambda: "client-request")
            assert await client.call("get_book", {"token_id": "11"}) == {
                "token_id": "11", "midpoint": "0.4200",
            }
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
