from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
UNIT = ROOT / "deploy" / "polymarket-hermes.service"
INSTALLER = ROOT / "deploy" / "install.sh"
CONFIG = ROOT / "deploy" / "config.example.toml"


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
    assert "--profile polymarket gateway run --replace" in text
    assert "After=network-online.target polymarket-ingestion.service" in text
    assert "Requisite=polymarket-ingestion.service" in text
    assert "PartOf=polymarket-ingestion.service" in text
    assert "Requires=polymarket-ingestion.service" not in text
    assert "Wants=polymarket-ingestion.service" not in text
    assert "ExecStartPre=/usr/bin/test -S /run/polybot-proposal/proposal.sock" in text
    assert "Restart=on-failure" in text
    assert "NoNewPrivileges=true" in text
    assert "ProtectSystem=strict" in text
    assert "ReadWritePaths=/root/.hermes/profiles/polymarket" in text
    assert "ReadWritePaths=/root/.hermes/auth.json" in text
    assert "InaccessiblePaths=-/root/.ssh" in text
    assert "InaccessiblePaths=-/root/.codex" in text
    assert "InaccessiblePaths=-/root/.hermes/profiles/coder" in text
    assert "InaccessiblePaths=-/opt/polymarket-bot/config.toml" in text
    assert "InaccessiblePaths=-/opt/polymarket-bot/.env" in text
    assert "InaccessiblePaths=-/opt/polymarket-bot/data" in text
    assert "WantedBy=multi-user.target" in text


def test_code_installer_installs_mcp_and_both_units_but_leaves_both_stopped():
    text = INSTALLER.read_text(encoding="utf-8")

    assert '"mcp==1.26.0"' in text
    assert "BRAIN_USER" not in text
    assert "/var/lib/polybot-hermes" not in text
    assert "polybot-proposal" in text
    assert "usermod -a -G \"$BRIDGE_GROUP\" \"$SVC_USER\"" in text
    assert "polymarket-hermes.service" in text
    assert "systemctl disable --now polymarket-ingestion.service polymarket-hermes.service" in text
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


def test_composite_example_configures_only_the_group_scoped_local_endpoint():
    text = CONFIG.read_text(encoding="utf-8")

    assert 'proposal_socket_path = "/run/polybot-proposal/proposal.sock"' in text
    assert 'proposal_socket_group = "polybot-proposal"' in text
    assert "proposal_max_per_minute = 20" in text
    assert "proposal_request_timeout_seconds = 2.0" in text
    assert "proposal_http" not in text
    assert "proposal_tcp" not in text
