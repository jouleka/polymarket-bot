import json

from polybot.runtime.config import IngestionConfig
from polybot.runtime.shadow_adapters import make_gamma_snapshot_fetch


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
