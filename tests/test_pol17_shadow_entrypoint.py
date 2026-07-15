import inspect
from types import SimpleNamespace

from polybot.runtime import shadow


def test_production_builder_wires_paper_root_and_transfers_adapter_ownership():
    trace = []
    gamma = SimpleNamespace(close=lambda: trace.append("gamma_close"))
    providers = (SimpleNamespace(provider_id="a"), SimpleNamespace(provider_id="b"))
    provider_close = lambda: trace.append("provider_close")
    built = SimpleNamespace(close_unstarted=lambda: trace.append("runtime_close"))
    config = SimpleNamespace(
        ingestion=SimpleNamespace(db_path="/data/events.db"),
        rpc_timeout_seconds=5,
        database_paths=("/data/events.db",),
    )

    def root_builder(received, **kwargs):
        assert received is config
        assert kwargs["gamma_snapshot_fetch"] is gamma
        assert kwargs["resolution_providers"] is providers
        assert kwargs["history_stamper"].stamp() > 0
        assert kwargs["health_stamper"].stamp() > 0
        assert len(kwargs["extra_closers"]) == 2
        trace.append("build")
        return built

    runtime = shadow.build_production_runtime(
        config,
        gamma_factory=lambda _config: gamma,
        provider_factory=lambda _config: (providers, provider_close),
        history_stamper_factory=lambda _path: SimpleNamespace(stamp=lambda: 10),
        health_stamper_factory=lambda: SimpleNamespace(stamp=lambda: 20),
        news_fetch_factory=lambda **_kwargs: object(),
        lock_factory=lambda _path: SimpleNamespace(
            acquire=lambda: None, release=lambda: None,
        ),
        readiness_factory=lambda: object(),
        root_builder=root_builder,
    )

    assert runtime is built
    assert trace == ["build"]
    assert not ({"signer", "wallet", "key", "order_client"}
                & set(inspect.signature(shadow.build_production_runtime).parameters))


def test_production_builder_acquires_singleton_before_any_adapter_or_store():
    trace = []
    lock = SimpleNamespace(
        acquire=lambda: trace.append("lock"),
        release=lambda: trace.append("unlock"),
    )
    gamma = SimpleNamespace(close=lambda: trace.append("gamma_close"))
    runtime = object()
    config = SimpleNamespace(
        ingestion=SimpleNamespace(db_path="/data/events.db"),
        rpc_timeout_seconds=5,
        database_paths=("/data/events.db",),
    )

    built = shadow.build_production_runtime(
        config,
        gamma_factory=lambda _config: trace.append("gamma") or gamma,
        provider_factory=lambda _config: (
            trace.append("providers") or (object(), object()),
            lambda: trace.append("providers_close"),
        ),
        history_stamper_factory=lambda _path: trace.append("history") or object(),
        health_stamper_factory=lambda: object(),
        news_fetch_factory=lambda **_kwargs: object(),
        lock_factory=lambda _path: lock,
        readiness_factory=lambda: object(),
        root_builder=lambda _config, **kwargs: (
            trace.append("stores") or runtime
            if kwargs["lock_acquired"] is True else None
        ),
    )

    assert built is runtime
    assert trace[:5] == ["lock", "gamma", "providers", "history", "stores"]
