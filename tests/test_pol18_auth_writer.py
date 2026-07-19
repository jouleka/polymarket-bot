import json
import os
from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parents[1]
HERMES_PYTHON = "/usr/local/lib/hermes-agent/venv/bin/python"


def test_native_auth_writer_atomically_updates_only_fixed_store(
        monkeypatch, tmp_path):
    from polybot.hermes import auth_writer

    home = tmp_path / "home"
    hermes_root = home / ".hermes"
    hermes_root.mkdir(parents=True)
    auth_file = hermes_root / "auth.json"
    auth_file.write_text(json.dumps({
        "version": 1,
        "providers": {"unrelated": {"value": "preserve"}},
    }), encoding="utf-8")
    auth_file.chmod(0o600)
    socket_path = tmp_path / "writer.sock"
    script = """
import os
from pathlib import Path
import socket

from polybot.hermes import auth_writer
from hermes_cli.auth import _save_auth_store

auth_writer._ROOT_AUTH_FILE = Path(os.environ["ROOT_AUTH_FILE"])
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(os.environ["AUTH_WRITER_SOCKET"])
listener.listen(1)
print("ready", flush=True)
connection, _ = listener.accept()
with connection:
    auth_writer.serve_connection(connection, save_auth_store=_save_auth_store)
listener.close()
"""
    env = {
        "AUTH_WRITER_SOCKET": str(socket_path),
        "HOME": str(home),
        "HERMES_HOME": str(hermes_root),
        "PYTHONPATH": f"{ROOT / 'src'}:/usr/local/lib/hermes-agent",
        "ROOT_AUTH_FILE": str(auth_file),
    }
    process = subprocess.Popen(
        [HERMES_PYTHON, "-c", script],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    assert process.stdout is not None
    assert process.stdout.readline().strip() == "ready"

    monkeypatch.setattr(auth_writer, "_AUTH_WRITER_SOCKET", socket_path)
    monkeypatch.setattr(auth_writer, "_ROOT_AUTH_FILE", auth_file)
    updated = {
        "version": 1,
        "providers": {
            "unrelated": {"value": "preserve"},
            "openai-codex": {
                "tokens": {
                    "access_token": "dummy-access",
                    "refresh_token": "dummy-refresh",
                },
            },
        },
    }
    assert auth_writer.write_auth_store(updated) == auth_file
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)

    persisted = json.loads(auth_file.read_text(encoding="utf-8"))
    assert persisted["providers"] == updated["providers"]
    assert auth_file.stat().st_mode & 0o777 == 0o600
    assert not list(hermes_root.glob("auth.json.tmp.*"))
