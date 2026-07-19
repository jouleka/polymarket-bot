from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "polymarket-hermes.service"
AUTH_WRITER_SOCKET = ROOT / "deploy" / "polymarket-hermes-auth-writer.socket"
AUTH_WRITER_SERVICE = ROOT / "deploy" / "polymarket-hermes-auth-writer@.service"
INSTALLER = ROOT / "deploy" / "install.sh"
CONFIG = ROOT / "deploy" / "config.example.toml"
RUNBOOK = ROOT / "deploy" / "hermes" / "README.md"
PYPROJECT = ROOT / "pyproject.toml"


def test_brain_unit_uses_existing_root_hermes_profile_and_does_not_activate_pol17():
    text = UNIT.read_text(encoding="utf-8")

    assert "User=root" in text
    assert "Group=root" in text
    assert "SupplementaryGroups=polybot-proposal" in text
    assert "WorkingDirectory=/root/.hermes/profiles/polymarket" in text
    assert "Environment=HOME=/root" in text
    assert "--profile-home /root/.hermes/profiles/polymarket" in text
    assert "/var/lib/polybot-hermes" not in text
    assert "EnvironmentFile=" not in text
    assert "profile_verify --profile-home" in text
    assert "profile_gateway --profile-home /root/.hermes/profiles/polymarket" in text
    assert "Environment=HERMES_KANBAN_DISPATCH_IN_GATEWAY=0" in text
    assert "profile_stop --profile-home /root/.hermes/profiles/polymarket" in text
    assert "InaccessiblePaths=-/root/.hermes/.env" in text
    assert "InaccessiblePaths=-/root/.hermes/config.yaml" in text
    assert "InaccessiblePaths=-/root/.hermes/gateway.json" in text
    assert "InaccessiblePaths=-/etc/hermes" in text
    assert "InaccessiblePaths=-/usr/local/lib/hermes-agent/.env" in text
    assert "After=network-online.target polymarket-ingestion.service" in text
    assert "Requisite=polymarket-ingestion.service" in text
    assert "PartOf=polymarket-ingestion.service" in text
    assert "Requires=polymarket-ingestion.service" not in text
    assert "Wants=polymarket-ingestion.service" not in text
    assert "ExecStartPre=/usr/bin/test -S /run/polybot-proposal/proposal.sock" in text
    assert "Restart=on-failure" in text
    assert "MemoryAccounting=true" in text
    assert "MemoryHigh=320M" in text
    assert "MemoryMax=512M" in text
    assert "MemorySwapMax=128M" in text
    assert "OOMPolicy=stop" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=/root/.hermes/profiles/polymarket" in text
    assert "ReadWritePaths=/root/.hermes/auth.json" not in text
    assert "InaccessiblePaths=-/root/.ssh" in text
    assert "InaccessiblePaths=-/root/.codex" in text
    assert "InaccessiblePaths=-/root/.hermes/profiles/coder" in text
    assert "InaccessiblePaths=-/opt/polymarket-bot/config.toml" in text
    assert "InaccessiblePaths=-/opt/polymarket-bot/.env" in text
    assert "InaccessiblePaths=-/opt/polymarket-bot/data" in text
    assert "WantedBy=multi-user.target" in text


def test_brain_unit_delegates_atomic_auth_without_shared_root_write_access():
    lines = UNIT.read_text(encoding="utf-8").splitlines()
    writer = AUTH_WRITER_SERVICE.read_text(encoding="utf-8").splitlines()
    writer_socket = AUTH_WRITER_SOCKET.read_text(encoding="utf-8").splitlines()

    assert "ReadWritePaths=/root/.hermes" not in lines
    assert "Requires=polymarket-hermes-auth-writer.socket" in lines
    assert "ReadWritePaths=/root/.hermes" in writer
    assert "RestrictAddressFamilies=AF_UNIX" in writer
    assert "StandardInput=socket" in writer
    assert "ExecStart=/usr/local/lib/hermes-agent/venv/bin/python -m polybot.hermes.auth_writer" in writer
    assert "ListenStream=/run/polymarket-hermes-auth-writer.sock" in writer_socket
    assert "SocketMode=0600" in writer_socket
    assert "Accept=yes" in writer_socket
    assert "MaxConnections=1" in writer_socket
    assert "PartOf=polymarket-hermes.service" in writer_socket
    assert "NoNewPrivileges=true" in writer
    assert "ProtectSystem=strict" in writer
    assert "MemoryMax=128M" in writer
    assert "MemorySwapMax=0" in writer
    assert "RuntimeMaxSec=20" in writer
    assert "TimeoutStopSec=5" in writer
    assert "InaccessiblePaths=-/root/.hermes/.env" in writer
    assert "InaccessiblePaths=-/root/.hermes/config.yaml" in writer
    assert "InaccessiblePaths=-/root/.hermes/profiles" in writer

    client = (ROOT / "src" / "polybot" / "hermes" / "auth_writer.py").read_text(
        encoding="utf-8"
    )
    assert client.index("connection.settimeout(5.0)") < client.index(
        "connection.connect"
    ) < client.index("connection.settimeout(None)") < client.index(
        "connection.sendall"
    )
    assert "ReadWritePaths=/root/.hermes/profiles/polymarket" in lines


def test_code_installer_installs_mcp_and_both_units_but_leaves_both_stopped():
    text = INSTALLER.read_text(encoding="utf-8")
    project = PYPROJECT.read_text(encoding="utf-8")

    assert '"mcp==1.28.1"' in text
    assert '"mcp==1.28.1"' in project
    assert '"mcp==1.26.0"' not in project
    assert "BRAIN_USER" not in text
    assert "/var/lib/polybot-hermes" not in text
    assert "polybot-proposal" in text
    assert "usermod -a -G \"$BRIDGE_GROUP\" \"$SVC_USER\"" in text
    assert "polymarket-hermes.service" in text
    assert 'cp "$APP/deploy/polymarket-hermes-auth-writer.socket"' in text
    assert 'cp "$APP/deploy/polymarket-hermes-auth-writer@.service"' in text
    assert "systemctl disable --now polymarket-ingestion.service polymarket-hermes.service polymarket-hermes-auth-writer.socket" in text
    assert "systemctl enable" not in text
    assert "systemctl start" not in text
    assert "hermes profile create" not in text
    assert 'chmod 0750 "$APP/data"' in text
    assert 'chmod 0640 "$APP/config.toml"' in text
    assert 'chown root:"$SVC_USER" "$APP/config.toml"' in text
    assert 'chmod 0640 "$APP/.env"' in text
    assert text.index("verify_services_not_active") < text.index(
        'echo "== 1. runtime user + proposal-socket group =="'
    )
    assert 'systemctl show --property=ActiveState --value "$unit"' in text
    assert 'systemctl show --property=LoadState --value "$unit"' in text
    assert text.count('if [ "$active_state" != "inactive" ]') >= 1

    runbook = RUNBOOK.read_text(encoding="utf-8")
    assert "/usr/local/lib/hermes-agent/venv/bin/python" in runbook
    assert '"mcp==1.28.1"' in runbook
    assert "do not recreate Hermes" in runbook
    assert "profile:" in runbook


def test_composite_example_configures_only_the_group_scoped_local_endpoint():
    text = CONFIG.read_text(encoding="utf-8")

    assert 'proposal_socket_path = "/run/polybot-proposal/proposal.sock"' in text
    assert 'proposal_socket_group = "polybot-proposal"' in text
    assert "proposal_max_per_minute = 20" in text
    assert "proposal_request_timeout_seconds = 2.0" in text
    assert "proposal_http" not in text
    assert "proposal_tcp" not in text


def test_runbook_uses_native_profile_and_existing_auth_without_another_login():
    text = RUNBOOK.read_text(encoding="utf-8")

    assert "/root/.hermes/profiles/polymarket" in text
    assert "Native named profiles inherit the existing root Hermes provider store" in text
    assert "Do not run another device login" in text
    assert "unlink -- /root/.hermes/profiles/polymarket/auth.json" in text
    assert "test ! -e /root/.hermes/profiles/polymarket/auth.json" in text
    assert "/var/lib/polybot-hermes" not in text
    assert "sudo -u polybot-hermes" not in text
