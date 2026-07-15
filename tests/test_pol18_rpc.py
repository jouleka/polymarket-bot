import json
import asyncio
import os
from pathlib import Path
import socket
import time
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


def test_rpc_rejects_duplicate_keys_ambiguous_frames_and_oversized_requests():
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, RpcProtocolError

    facade = _Facade()
    dispatcher = ProposalRpcDispatcher(facade, max_request_bytes=256)
    duplicate = (
        b'{"version":1,"id":"a","id":"b","method":"get_flags","params":{}}\n'
    )
    for request in (duplicate, b'{}', b'{}\n{}\n', b'{' + b'x' * 256 + b'}\n'):
        with pytest.raises(RpcProtocolError):
            dispatcher.handle(request)
    assert facade.calls == []


def test_rpc_rejects_noncanonical_decimal_strings_before_the_facade():
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, RpcProtocolError

    for value in (" 0.42", "+0.42", "4.2e-1", "00.42", ".42", "0."):
        facade = _Facade()
        dispatcher = ProposalRpcDispatcher(facade)
        payload = json.loads(_proposal())
        payload["params"]["target_price"] = value

        with pytest.raises(RpcProtocolError, match="exact decimal string"):
            dispatcher.handle(_wire(payload))
        assert facade.calls == []


def test_rpc_rejects_non_utf8_scalar_text_before_the_facade():
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, RpcProtocolError

    facade = _Facade()
    payload = json.loads(_proposal())
    payload["params"]["thesis"] = "escaped-surrogate-\ud800"

    with pytest.raises(RpcProtocolError, match="bounded exact string"):
        ProposalRpcDispatcher(facade).handle(_wire(payload))
    assert facade.calls == []


def test_rpc_rejects_invalid_or_format_control_text_before_any_write():
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, RpcProtocolError

    for field, value in (("id", "bad-\ud800"), ("thesis", "hidden\u202econtrol")):
        facade = _Facade()
        payload = json.loads(_proposal())
        if field == "id":
            payload["id"] = value
        else:
            payload["params"][field] = value

        with pytest.raises(RpcProtocolError, match="bounded exact string"):
            ProposalRpcDispatcher(facade).handle(_wire(payload))
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


def test_unix_server_rejects_requests_until_runtime_readiness_is_true(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "readiness.sock"
        ready = [False]
        facade = _Facade()
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(facade), runtime_ready=lambda: ready[0],
        )
        task = asyncio.create_task(server.run())
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(_wire({
                "version": 1, "id": "not-ready", "method": "get_book",
                "params": {"token_id": "11"},
            }))
            await writer.drain()
            response = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            assert response["error"]["code"] == "runtime_not_ready"
            assert facade.calls == []

            ready[0] = True
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(_wire({
                "version": 1, "id": "ready", "method": "get_book",
                "params": {"token_id": "11"},
            }))
            await writer.drain()
            response = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            assert response["result"]["token_id"] == "11"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_unix_server_fences_preconnected_clients_before_shutdown(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "shutdown.sock"
        facade = _Facade()
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(facade), runtime_ready=lambda: True,
            request_timeout_seconds=0.5,
        )
        task = asyncio.create_task(server.run())
        await asyncio.wait_for(server.started.wait(), timeout=1)
        reader, writer = await asyncio.open_unix_connection(path)
        while not server._client_tasks:
            await asyncio.sleep(0)

        task.cancel()
        while server._accepting_requests:
            await asyncio.sleep(0)
        response = None
        try:
            writer.write(_wire({
                "version": 1, "id": "too-late", "method": "get_book",
                "params": {"token_id": "11"},
            }))
            await writer.drain()
            frame = await asyncio.wait_for(reader.readline(), timeout=0.2)
            if frame:
                response = json.loads(frame)
        except ConnectionError:
            pass
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
        with pytest.raises(asyncio.CancelledError):
            await task

        if response is not None:
            assert response["error"]["code"] == "server_stopping"
        assert facade.calls == []
        assert not path.exists()

    import pytest
    asyncio.run(scenario())


def test_unix_server_halts_when_a_handler_escapes_its_isolation_boundary(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "fatal-handler.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )

        async def escaped_handler(_reader, _writer):
            raise RuntimeError("escaped handler")

        server._accept = escaped_handler
        task = asyncio.create_task(server.run())
        await asyncio.wait_for(server.started.wait(), timeout=1)
        _reader, writer = await asyncio.open_unix_connection(path)
        with pytest.raises(RuntimeError, match="handler escaped"):
            await asyncio.wait_for(task, timeout=1)
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass
        assert not path.exists()

    import pytest
    asyncio.run(scenario())


def test_unix_server_halts_instead_of_acknowledging_an_overdue_sync_dispatch(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    class SlowFacade(_Facade):
        def get_book(self, **params):
            time.sleep(0.05)
            return super().get_book(**params)

    async def scenario():
        path = tmp_path / "overdue.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(SlowFacade()), runtime_ready=lambda: True,
            request_timeout_seconds=0.01,
        )
        task = asyncio.create_task(server.run())
        await asyncio.wait_for(server.started.wait(), timeout=1)
        reader, writer = await asyncio.open_unix_connection(path)
        writer.write(_wire({
            "version": 1, "id": "overdue", "method": "get_book",
            "params": {"token_id": "11"},
        }))
        await writer.drain()
        assert await asyncio.wait_for(reader.readline(), timeout=0.2) == b""
        with pytest.raises(RuntimeError, match="handler escaped") as exc:
            await asyncio.wait_for(task, timeout=1)
        assert "deadline" in str(exc.value.__cause__)
        writer.close()
        try:
            await writer.wait_closed()
        except ConnectionError:
            pass

    import pytest
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


def test_unix_server_rejects_active_non_socket_and_stale_identity_collisions(tmp_path):
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def active_collision():
        path = tmp_path / "active.sock"
        first = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )
        task = asyncio.create_task(first.run())
        try:
            await asyncio.wait_for(first.started.wait(), timeout=1)
            second = ProposalRpcServer(
                path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
            )
            with pytest.raises(RuntimeError, match="already accepting"):
                await second.run()
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(active_collision())

    collision = tmp_path / "not-a-socket"
    collision.write_text("preserve", encoding="utf-8")
    server = ProposalRpcServer(
        collision, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
    )
    with pytest.raises(RuntimeError, match="non-socket"):
        asyncio.run(server.run())
    assert collision.read_text(encoding="utf-8") == "preserve"

    path = tmp_path / "identity.sock"
    original = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    original.bind(str(path))
    observed = path.lstat()
    old_path = tmp_path / "old-identity.sock"
    path.rename(old_path)
    original.close()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    replacement.bind(str(path))
    try:
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )
        with pytest.raises(RuntimeError, match="changed"):
            server._remove_proven_stale_socket(observed)
        assert path.exists()
    finally:
        replacement.close()
        path.unlink(missing_ok=True)
        old_path.unlink(missing_ok=True)


def test_unix_server_rejects_excess_clients_without_queueing_handlers(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "bounded.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
            max_concurrent_requests=1, request_timeout_seconds=1.0,
        )
        task = asyncio.create_task(server.run())
        first_writer = second_writer = None
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            _first_reader, first_writer = await asyncio.open_unix_connection(path)
            second_reader, second_writer = await asyncio.open_unix_connection(path)
            second_writer.write(_wire({
                "version": 1,
                "id": "over-limit",
                "method": "get_book",
                "params": {"token_id": "11"},
            }))
            await second_writer.drain()

            response = json.loads(
                await asyncio.wait_for(second_reader.readline(), timeout=0.2)
            )
            assert response["error"]["code"] == "server_busy"
        finally:
            for writer in (first_writer, second_writer):
                if writer is not None:
                    writer.close()
                    try:
                        await writer.wait_closed()
                    except ConnectionError:
                        pass
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_unix_server_times_out_an_idle_admitted_client(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "idle.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
            request_timeout_seconds=0.05,
        )
        task = asyncio.create_task(server.run())
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            reader, writer = await asyncio.open_unix_connection(path)
            response = json.loads(await asyncio.wait_for(reader.readline(), timeout=0.2))
            writer.close()
            await writer.wait_closed()
            assert response["error"]["code"] == "request_timeout"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())


def test_unix_server_rejects_a_symlinked_socket_directory(tmp_path):
    import pytest

    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "redirected"
    link.symlink_to(target, target_is_directory=True)
    server = ProposalRpcServer(
        link / "proposal.sock", ProposalRpcDispatcher(_Facade()),
        runtime_ready=lambda: True,
    )

    with pytest.raises(RuntimeError, match="socket directory"):
        asyncio.run(server.run())
    assert not (target / "proposal.sock").exists()


def test_unix_server_never_publishes_when_socket_ownership_setup_fails(
        tmp_path, monkeypatch):
    import pytest

    import polybot.hermes.rpc as rpc

    path = tmp_path / "proposal.sock"
    server = rpc.ProposalRpcServer(
        path, rpc.ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        socket_group=os.getgid(),
    )
    published_during_setup = []
    attachable_during_setup = []
    staging_modes = []

    def reject_chown(staged_path, *_args):
        published_during_setup.append(path.exists())
        staged_path = Path(staged_path)
        staging_modes.append(staged_path.parent.stat().st_mode & 0o777)
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        probe.settimeout(0.1)
        try:
            probe.connect(str(staged_path))
        except OSError:
            attachable_during_setup.append(False)
        else:
            attachable_during_setup.append(True)
        finally:
            probe.close()
        raise PermissionError("injected chown failure")

    monkeypatch.setattr(rpc.os, "chown", reject_chown)
    with pytest.raises(PermissionError, match="injected chown failure"):
        asyncio.run(server.run())

    assert published_during_setup == [False]
    assert attachable_during_setup == [False]
    assert staging_modes == [0o700]
    assert not path.exists()
    assert not list(tmp_path.glob(".p-*"))


def test_unix_server_cleanup_preserves_a_replacement_socket_inode(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    async def scenario():
        path = tmp_path / "owned.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )
        task = asyncio.create_task(server.run())
        replacement = None
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            path.unlink()
            replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            replacement.bind(str(path))
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            assert path.exists()
        finally:
            if replacement is not None:
                replacement.close()
            path.unlink(missing_ok=True)

    import pytest
    asyncio.run(scenario())


def test_unix_rpc_rejects_an_overlong_socket_path_before_startup(tmp_path):
    import pytest

    from polybot.hermes.rpc import (
        ProposalRpcClient, ProposalRpcDispatcher, ProposalRpcServer,
    )

    path = tmp_path / ("x" * 128)
    with pytest.raises(ValueError, match="socket path"):
        ProposalRpcServer(
            path, ProposalRpcDispatcher(_Facade()), runtime_ready=lambda: True,
        )
    with pytest.raises(ValueError, match="socket path"):
        ProposalRpcClient(path)


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


def test_unix_client_fails_closed_for_absent_and_invalid_endpoints(tmp_path):
    import pytest

    from polybot.hermes.rpc import ProposalRpcClient, RpcRemoteError

    async def scenario():
        missing = ProposalRpcClient(tmp_path / "missing.sock")
        with pytest.raises(RpcRemoteError, match="transport_unavailable"):
            await missing.call("get_flags", {})

        path = tmp_path / "invalid.sock"

        async def invalid_response(_reader, writer):
            writer.write(b"not-json\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_unix_server(invalid_response, path=path)
        try:
            with pytest.raises(RpcRemoteError, match="invalid_response"):
                await ProposalRpcClient(path).call("get_flags", {})
        finally:
            server.close()
            await server.wait_closed()

    asyncio.run(scenario())


def test_unix_server_rejects_a_response_over_its_byte_ceiling(tmp_path):
    from polybot.hermes.rpc import ProposalRpcDispatcher, ProposalRpcServer

    class LargeFacade(_Facade):
        def get_book(self, **params):
            return {"token_id": params["token_id"], "payload": "x" * 500}

    async def scenario():
        path = tmp_path / "response-limit.sock"
        server = ProposalRpcServer(
            path, ProposalRpcDispatcher(LargeFacade()), runtime_ready=lambda: True,
            max_response_bytes=128,
        )
        task = asyncio.create_task(server.run())
        try:
            await asyncio.wait_for(server.started.wait(), timeout=1)
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(_wire({
                "version": 1, "id": "large", "method": "get_book",
                "params": {"token_id": "11"},
            }))
            await writer.drain()
            response = json.loads(await reader.readline())
            writer.close()
            await writer.wait_closed()
            assert response["error"]["code"] == "request_rejected"
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    asyncio.run(scenario())
