import inspect
import json
import re
from types import SimpleNamespace

import pytest

from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_adapters import (
    make_gamma_snapshot_fetch,
    make_resolution_providers,
    SingletonLock,
    SystemdReadiness,
    StopAwareResolutionProvider,
)
from polybot.resolution.errors import ResolutionUnavailable
from polybot.resolution.models import ResolutionSubject
from polybot.resolution.rpc import JsonRpcClient, JsonRpcResolutionProvider
from polybot.runtime.shadow_config import (
    ReadOnlyPolygonProviderConfig,
    ShadowRuntimeConfig,
)


def _market(condition_id, tokens, event_id, volume):
    return {
        "conditionId": condition_id,
        "question": f"Will {condition_id}?",
        "slug": condition_id,
        "endDate": "2030-01-01T00:00:00Z",
        "clobTokenIds": json.dumps(tokens),
        "outcomes": '["Yes", "No"]',
        "outcomePrices": '["0.5", "0.5"]',
        "events": [{"id": event_id}],
        "acceptingOrders": True,
        "active": True,
        "closed": False,
        "volume24hr": volume,
    }


def test_gamma_snapshot_fetcher_freezes_selected_condition_ids_on_refresh():
    high = _market("c-high", ["t1", "t2"], "11", 20)
    low = _market("c-low", ["t3", "t4"], "12", 10)
    calls = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class Client:
        def get(self, path, *, params):
            calls.append((path, params))
            if path == "/markets" and "condition_ids" not in params:
                return Response([low, high])
            if path == "/markets":
                assert params["condition_ids"] == ("c-high",)
                return Response([{**high, "question": "Updated"}])
            assert path == "/events"
            return Response([{
                "id": "11",
                "tags": [{"id": "2"}],
                "markets": [{
                    "conditionId": "c-high",
                    "clobTokenIds": json.dumps(["t1", "t2"]),
                }],
            }])

    fetch = make_gamma_snapshot_fetch(
        IngestionConfig(db_path="/tmp/events.db", universe_max_markets=1),
        client=Client(),
    )

    first = fetch()
    second = fetch()

    assert [row["conditionId"] for row in first[0]] == ["c-high"]
    assert second[0][0]["question"] == "Updated"
    assert [call[0] for call in calls] == [
        "/markets", "/events", "/markets", "/events",
    ]


def test_resolution_provider_factory_owns_exactly_two_timed_read_only_clients():
    made = []

    class Client:
        def __init__(self, *, timeout):
            self.timeout = timeout
            self.closed = False
            made.append(self)

        def post(self, *_args, **_kwargs):
            raise AssertionError("construction must not call Polygon")

        def close(self):
            self.closed = True

    config = ShadowRuntimeConfig(
        ingestion=IngestionConfig(db_path="/data/events.db"),
        intents_db_path="/data/intents.db",
        forecasts_db_path="/data/forecasts.db",
        components_db_path="/data/components.db",
        maker_db_path="/data/maker.db",
        shadow_db_path="/data/shadow.db",
        resolution_db_path="/data/resolution.db",
        polygon_providers=(
            ReadOnlyPolygonProviderConfig("a", "https://a.example"),
            ReadOnlyPolygonProviderConfig("b", "https://b.example"),
        ),
        rpc_timeout_seconds=7.5,
    )

    providers, close = make_resolution_providers(config, client_factory=Client)

    assert [provider.provider_id for provider in providers] == ["a", "b"]
    assert [client.timeout for client in made] == [7.5, 7.5]
    close()
    assert all(client.closed for client in made)


def test_singleton_lock_is_nonblocking_and_reusable_after_release(tmp_path):
    path = str(tmp_path / "shadow.lock")
    first = SingletonLock(path)
    second = SingletonLock(path)

    first.acquire()
    with pytest.raises(RuntimeError, match="already running"):
        second.acquire()
    first.release()

    second.acquire()
    second.release()


def test_systemd_readiness_sends_native_abstract_socket_notifications():
    sent = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            pass

        def connect(self, address):
            sent.append(("connect", address))

        def sendall(self, payload):
            sent.append(("send", payload))

    readiness = SystemdReadiness(
        environ={"NOTIFY_SOCKET": "@polybot"},
        socket_factory=lambda *_args: Socket(),
    )

    readiness.ready()
    readiness.stopping()

    assert sent == [
        ("connect", "\0polybot"), ("send", b"READY=1"),
        ("connect", "\0polybot"), ("send", b"STOPPING=1"),
    ]


def test_stop_aware_provider_never_starts_another_rpc_after_shutdown():
    calls = []
    stopped = [False]
    provider = SimpleNamespace(
        provider_id="a",
        chain_id=lambda: calls.append("chain") or 137,
        latest_block=lambda: calls.append("head") or 100,
    )
    wrapped = StopAwareResolutionProvider(provider, lambda: stopped[0])

    assert wrapped.chain_id() == 137
    stopped[0] = True
    with pytest.raises(ResolutionUnavailable, match="stopping"):
        wrapped.latest_block()

    assert calls == ["chain"]


def test_stop_gate_is_rechecked_before_each_rpc_inside_one_observation():
    stopped = [False]
    requests = []

    class Response:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self._payload

    class Client:
        def post(self, _endpoint, *, json):
            requests.append(json["method"])
            stopped[0] = True
            return Response({
                "jsonrpc": "2.0", "id": json["id"],
                "result": {
                    "number": "0x64",
                    "hash": "0x" + "11" * 32,
                },
            })

    provider = JsonRpcResolutionProvider(
        "a", JsonRpcClient("https://a.example", Client())
    )
    wrapped = StopAwareResolutionProvider(provider, lambda: stopped[0])
    subject = ResolutionSubject(
        event_id="event-1", condition_id="0x" + "22" * 32,
        token_ids=("101", "202"), category="politics",
    )

    with pytest.raises(ResolutionUnavailable, match="stopping"):
        wrapped.observe(subject, 100)

    assert requests == ["eth_getBlockByNumber"]


def test_polygon_resolution_adapter_has_only_read_only_rpc_vocabulary():
    source = inspect.getsource(JsonRpcResolutionProvider)
    methods = set(re.findall(r'_rpc\.call\(\s*"([^"]+)"', source))

    assert methods == {
        "eth_chainId", "eth_blockNumber", "eth_getBlockByNumber", "eth_getCode",
        "eth_getLogs", "eth_call",
    }
    lowered = source.lower()
    for forbidden in (
        "eth_sendtransaction", "eth_sendrawtransaction", "personal_sign",
        "eth_sign", "wallet", "private_key", "cancel", "redeem",
    ):
        assert forbidden not in lowered
