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
        lock_factory=lambda _path: object(),
        readiness_factory=lambda: object(),
        root_builder=root_builder,
    )

    assert runtime is built
    assert trace == ["build"]
    assert not ({"signer", "wallet", "key", "order_client"}
                & set(inspect.signature(shadow.build_production_runtime).parameters))
