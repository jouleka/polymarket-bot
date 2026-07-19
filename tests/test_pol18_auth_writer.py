import json
import inspect
import os
from pathlib import Path
import socket
import subprocess
import threading

import pytest


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
        "active_provider": "unrelated",
        "providers": {
            "unrelated": {"value": "preserve"},
            "openai-codex": {"tokens": {"access_token": "old"}},
        },
        "credential_pool": {
            "unrelated": [{"id": "preserve"}],
            "openai-codex": [{"id": "codex", "access_token": "old"}],
        },
    }), encoding="utf-8")
    auth_file.chmod(0o600)
    socket_path = tmp_path / "writer.sock"
    script = """
import os
from pathlib import Path
import socket

from polybot.hermes import auth_writer
from hermes_cli.auth import _load_auth_store, _save_auth_store

auth_writer._ROOT_AUTH_FILE = Path(os.environ["ROOT_AUTH_FILE"])
listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
listener.bind(os.environ["AUTH_WRITER_SOCKET"])
listener.listen(1)
print("ready", flush=True)
connection, _ = listener.accept()
with connection:
    auth_writer.serve_connection(
        connection,
        load_auth_store=_load_auth_store,
        save_auth_store=_save_auth_store,
    )
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
        "active_provider": "unrelated",
        "providers": {
            "unrelated": {"value": "preserve"},
            "openai-codex": {
                "tokens": {
                    "access_token": "dummy-access",
                    "refresh_token": "dummy-refresh",
                },
            },
        },
        "credential_pool": {
            "unrelated": [{"id": "preserve"}],
            "openai-codex": [{"id": "codex", "access_token": "new"}],
        },
    }
    assert auth_writer.write_auth_store(updated) == auth_file
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)

    persisted = json.loads(auth_file.read_text(encoding="utf-8"))
    assert persisted["providers"] == updated["providers"]
    assert persisted["active_provider"] == "unrelated"
    assert persisted["credential_pool"]["unrelated"] == [{"id": "preserve"}]
    assert auth_file.stat().st_mode & 0o777 == 0o600
    assert not list(hermes_root.glob("auth.json.tmp.*"))


@pytest.mark.parametrize("unsafe", ["target", "shape", "key", "size"])
def test_auth_writer_client_rejects_unreviewed_requests(
        monkeypatch, tmp_path, unsafe):
    from polybot.hermes import auth_writer

    root_auth = tmp_path / "auth.json"
    monkeypatch.setattr(auth_writer, "_ROOT_AUTH_FILE", root_auth)
    store = {"providers": {}}
    target = None
    if unsafe == "target":
        target = tmp_path / "other.json"
    elif unsafe == "shape":
        store = {"providers": []}
    elif unsafe == "key":
        store = {"providers": {}, "target": "/tmp/unsafe"}
    elif unsafe == "size":
        store = {"providers": {"dummy": {"value": "x" * (1024 * 1024)}}}

    with pytest.raises(RuntimeError, match="auth writer"):
        auth_writer.write_auth_store(store, target_path=target)


def test_auth_writer_server_rejects_non_root_peer_before_read_or_save():
    from polybot.hermes import auth_writer

    class NonRootConnection:
        sent = []

        def getsockopt(self, *args):
            return auth_writer._PEER_CREDENTIALS.pack(123, 1000, 1000)

        def recv(self, size):
            raise AssertionError("non-root request must not be read")

        def sendall(self, payload):
            self.sent.append(payload)

    connection = NonRootConnection()
    with pytest.raises(RuntimeError, match="non-root peer"):
        auth_writer.serve_connection(
            connection,
            load_auth_store=lambda *args, **kwargs: pytest.fail(
                "non-root request must not load"
            ),
            save_auth_store=lambda *args, **kwargs: pytest.fail(
                "non-root request must not save"
            ),
        )
    assert connection.sent == [b"ER"]


def test_auth_writer_preserves_every_non_codex_root_credential(
        monkeypatch, tmp_path):
    from polybot.hermes import auth_writer

    root_auth = tmp_path / "auth.json"
    writer_socket = tmp_path / "writer.sock"
    current = {
        "version": 1,
        "active_provider": "unrelated",
        "providers": {
            "unrelated": {"api_key": "preserve"},
            "openai-codex": {"tokens": {"access_token": "old"}},
        },
        "credential_pool": {
            "unrelated": [{"id": "preserve"}],
            "openai-codex": [{"id": "codex"}],
        },
    }
    saved = []
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(writer_socket))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        kwargs = {
            "save_auth_store": lambda store, **unused: saved.append(store),
        }
        if "load_auth_store" in inspect.signature(
                auth_writer.serve_connection).parameters:
            kwargs["load_auth_store"] = lambda unused: current
        with connection:
            with pytest.raises(RuntimeError, match="non-Codex"):
                auth_writer.serve_connection(connection, **kwargs)

    server = threading.Thread(target=serve)
    server.start()
    monkeypatch.setattr(auth_writer, "_ROOT_AUTH_FILE", root_auth)
    monkeypatch.setattr(auth_writer, "_AUTH_WRITER_SOCKET", writer_socket)
    unsafe = {
        "version": 1,
        "active_provider": "unrelated",
        "providers": {
            "openai-codex": {"tokens": {"access_token": "new"}},
        },
        "credential_pool": {
            "openai-codex": [{"id": "codex"}],
        },
    }
    with pytest.raises(RuntimeError, match="refused persistence"):
        auth_writer.write_auth_store(unsafe)
    server.join(timeout=5)
    listener.close()
    assert not server.is_alive()
    assert saved == []


def test_auth_writer_allows_first_native_codex_pool_seed(
        monkeypatch, tmp_path):
    from polybot.hermes import auth_writer

    root_auth = tmp_path / "auth.json"
    writer_socket = tmp_path / "writer.sock"
    current = {
        "version": 1,
        "providers": {
            "unrelated": {"api_key": "preserve"},
            "openai-codex": {"tokens": {"access_token": "old"}},
        },
    }
    requested = {
        **current,
        "credential_pool": {
            "openai-codex": [{"id": "first", "access_token": "old"}],
        },
    }
    saved = []
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(writer_socket))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        with connection:
            auth_writer.serve_connection(
                connection,
                load_auth_store=lambda unused: current,
                save_auth_store=lambda store, **unused: saved.append(store),
            )

    server = threading.Thread(target=serve)
    server.start()
    monkeypatch.setattr(auth_writer, "_ROOT_AUTH_FILE", root_auth)
    monkeypatch.setattr(auth_writer, "_AUTH_WRITER_SOCKET", writer_socket)
    assert auth_writer.write_auth_store(requested) == root_auth
    server.join(timeout=5)
    listener.close()
    assert not server.is_alive()
    assert saved == [requested]
