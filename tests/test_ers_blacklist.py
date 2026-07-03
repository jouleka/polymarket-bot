"""S4.6d (POL-6): the durable append-only `blacklist` table on IntentStore.

The store is DUMB -- record_blacklist records ANY target_kind string (the
TelegramController.__apply validates the kind and raises BEFORE calling; the store
just records). Append-only + the shared monotonic stamp + commit-per-write, mirroring
record_fill/record_op_event. Helpers copied per file per convention (no conftest)."""

from polybot.core.clock import MonotonicStamper
from polybot.ers.intent_store import IntentStore


def _store(path):
    return IntentStore(path, MonotonicStamper())


def test_record_blacklist_round_trips_wallet_market_source_in_bl_id_order(tmp_path):
    # Kills: dropping the table/method, wrong ORDER BY (not bl_id), or column mis-mapping
    # (target_kind/target_value swapped). Three kinds recorded in a fixed order come back in
    # that exact order with a monotonic `at`.
    with _store(str(tmp_path / "i.db")) as store:
        store.record_blacklist(target_kind="wallet", target_value="0xabc")
        store.record_blacklist(target_kind="market", target_value="m-42")
        store.record_blacklist(target_kind="source", target_value="rss-7")

        rows = store.blacklist_log()
        assert [(r["target_kind"], r["target_value"]) for r in rows] == [
            ("wallet", "0xabc"), ("market", "m-42"), ("source", "rss-7")]
        ats = [r["at"] for r in rows]
        assert ats == sorted(ats) and len(set(ats)) == 3 and ats[0] > 0


def test_record_blacklist_is_dumb_and_records_an_unknown_kind_verbatim(tmp_path):
    # DESIGN Fork 2 + the pinned contract: the store does NOT validate target_kind (kind
    # validation lives in TelegramController.__apply, which raises BEFORE calling). Proves the
    # store persists a kind OUTSIDE {wallet,market,source} verbatim. Kills: sneaking a
    # kind-whitelist ValueError into record_blacklist (which would move policy into the store
    # and break the "dumb store" contract D3 relies on).
    with _store(str(tmp_path / "i.db")) as store:
        store.record_blacklist(target_kind="banana", target_value="whatever")
        rows = store.blacklist_log()
        assert [(r["target_kind"], r["target_value"]) for r in rows] == [
            ("banana", "whatever")]
