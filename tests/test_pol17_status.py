import json

from polybot.runtime.status import RuntimeStatusReporter


def test_runtime_status_is_atomically_replaced_and_mirrored_to_systemd(tmp_path):
    notices = []
    path = tmp_path / "shadow-status.json"
    reporter = RuntimeStatusReporter(
        str(path),
        readiness=type("Readiness", (), {
            "status": lambda _self, message: notices.append(message),
        })(),
        clock=lambda: 1_750_000_000.25,
    )

    reporter.update({"controller": "RUNNING", "pending_intents": 0})

    assert json.loads(path.read_text()) == {
        "controller": "RUNNING",
        "pending_intents": 0,
        "updated_at": 1_750_000_000.25,
    }
    assert notices == ["RUNNING; pending=0"]
    assert list(tmp_path.iterdir()) == [path]
