import os
import re
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "install.sh"
UNIT = ROOT / "deploy" / "polymarket-ingestion.service"
RUNBOOK = ROOT / "deploy" / "README.md"


def test_installer_leaves_service_stopped_and_disabled():
    text = INSTALLER.read_text()
    commands = [
        line.strip() for line in text.splitlines()
        if line.strip() and not line.lstrip().startswith(("#", "echo"))
    ]
    install = 'cp "$APP/deploy/polymarket-ingestion.service" /etc/systemd/system/polymarket-ingestion.service'
    install_brain = 'cp "$APP/deploy/polymarket-hermes.service" /etc/systemd/system/polymarket-hermes.service'
    reload = "systemctl daemon-reload"
    disable = "systemctl disable --now polymarket-ingestion.service polymarket-hermes.service polymarket-hermes-auth-writer.socket"
    verify = "verify_service_stopped_disabled"

    assert install in commands
    assert install_brain in commands
    assert reload in commands
    assert disable in commands
    assert verify in commands
    assert (commands.index(install) < commands.index(install_brain)
            < commands.index(reload) < commands.index(disable) < commands.index(verify))
    assert "for unit in polymarket-ingestion.service polymarket-hermes.service" in text
    assert 'systemctl is-active "$unit"' in text
    assert 'systemctl is-enabled "$unit"' in text
    assert all("|| true" not in line for line in commands if "disable --now" in line)
    assert not re.search(r"^\s*systemctl\s+(?:enable|reenable|start|restart)\b", text, re.MULTILINE)
    assert "installed; both services remain STOPPED + DISABLED" in text


def _run_installer_state_check(*, active, active_rc, enabled, enabled_rc):
    text = INSTALLER.read_text()
    start = text.index("verify_service_stopped_disabled() {")
    end = text.index("\n}\n", start) + len("\n}")
    function = text[start:end]
    harness = f'''\
systemctl() {{
    case "$1" in
        is-active) printf '%s\\n' "$MOCK_ACTIVE"; return "$MOCK_ACTIVE_RC" ;;
        is-enabled) printf '%s\\n' "$MOCK_ENABLED"; return "$MOCK_ENABLED_RC" ;;
        *) return 99 ;;
    esac
}}
{function}
verify_service_stopped_disabled
'''
    env = os.environ | {
        "MOCK_ACTIVE": active,
        "MOCK_ACTIVE_RC": str(active_rc),
        "MOCK_ENABLED": enabled,
        "MOCK_ENABLED_RC": str(enabled_rc),
    }
    return subprocess.run(
        ["bash", "-c", harness],
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_installer_state_check_accepts_only_inactive_and_disabled():
    result = _run_installer_state_check(
        active="inactive",
        active_rc=3,
        enabled="disabled",
        enabled_rc=1,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(("active", "active_rc", "enabled", "enabled_rc", "message"), [
    ("active", 0, "disabled", 1, "expected polymarket-ingestion.service inactive"),
    ("inactive", 3, "enabled", 0, "expected polymarket-ingestion.service disabled"),
])
def test_installer_state_check_rejects_unsafe_state(
        active, active_rc, enabled, enabled_rc, message):
    result = _run_installer_state_check(
        active=active,
        active_rc=active_rc,
        enabled=enabled,
        enabled_rc=enabled_rc,
    )
    assert result.returncode != 0
    assert message in result.stderr


def _run_preinstall_state_check(*, ingestion_active, ingestion_load,
                                brain_active, brain_load):
    text = INSTALLER.read_text()
    start = text.index("verify_services_not_active() {")
    end = text.index("\n}\n", start) + len("\n}")
    function = text[start:end]
    harness = f'''\
systemctl() {{
    case "$1:$2:$4" in
        show:--property=ActiveState:polymarket-ingestion.service)
            printf '%s\n' "$MOCK_INGESTION_ACTIVE"; return 0 ;;
        show:--property=LoadState:polymarket-ingestion.service)
            printf '%s\n' "$MOCK_INGESTION_LOAD"; return 0 ;;
        show:--property=ActiveState:polymarket-hermes.service)
            printf '%s\n' "$MOCK_BRAIN_ACTIVE"; return 0 ;;
        show:--property=LoadState:polymarket-hermes.service)
            printf '%s\n' "$MOCK_BRAIN_LOAD"; return 0 ;;
        show:--property=ActiveState:polymarket-hermes-auth-writer.socket)
            printf '%s\n' "$MOCK_BRAIN_ACTIVE"; return 0 ;;
        show:--property=LoadState:polymarket-hermes-auth-writer.socket)
            printf '%s\n' "$MOCK_BRAIN_LOAD"; return 0 ;;
        *) return 99 ;;
    esac
}}
{function}
verify_services_not_active
'''
    env = os.environ | {
        "MOCK_INGESTION_ACTIVE": ingestion_active,
        "MOCK_INGESTION_LOAD": ingestion_load,
        "MOCK_BRAIN_ACTIVE": brain_active,
        "MOCK_BRAIN_LOAD": brain_load,
    }
    return subprocess.run(
        ["bash", "-c", harness], env=env, text=True,
        capture_output=True, check=False,
    )


def test_preinstall_gate_accepts_only_inactive_ingestion_and_absent_new_brain_unit():
    first_install = _run_preinstall_state_check(
        ingestion_active="inactive", ingestion_load="loaded",
        brain_active="inactive", brain_load="not-found",
    )
    assert first_install.returncode == 0, first_install.stderr

    safe_rerun = _run_preinstall_state_check(
        ingestion_active="inactive", ingestion_load="loaded",
        brain_active="inactive", brain_load="loaded",
    )
    assert safe_rerun.returncode == 0, safe_rerun.stderr

    for ingestion_active, ingestion_load, brain_active, brain_load in (
        ("inactive", "not-found", "inactive", "not-found"),
        ("active", "loaded", "inactive", "not-found"),
        ("inactive", "loaded", "activating", "loaded"),
        ("inactive", "loaded", "failed", "loaded"),
        ("inactive", "loaded", "active", "not-found"),
    ):
        unsafe = _run_preinstall_state_check(
            ingestion_active=ingestion_active,
            ingestion_load=ingestion_load,
            brain_active=brain_active,
            brain_load=brain_load,
        )
        assert unsafe.returncode != 0
        assert "refusing install" in unsafe.stderr


def test_unit_describes_compact_midpoint_and_trade_persistence():
    description = next(
        line for line in UNIT.read_text().splitlines()
        if line.startswith("Description=")
    )
    assert "midpoint" in description
    assert "trade" in description
    assert "raw" not in description
    assert "un-backfillable order-book" not in description


def test_unit_runs_the_composite_shadow_runtime_with_notify_contract():
    text = UNIT.read_text()

    assert "Type=notify" in text
    assert "NotifyAccess=main" in text
    assert "python -m polybot.runtime.shadow" in text
    assert "Restart=on-failure" in text
    assert "RestartSec=5" in text
    assert "TimeoutStartSec=60" in text
    assert "TimeoutStopSec=60" in text
    assert "After=network-online.target" in text
    assert "Wants=network-online.target" in text
    assert "RuntimeDirectory=polybot polybot-proposal" in text
    assert "RuntimeDirectoryMode=0750" in text
    assert "User=polybot" in text
    assert "Group=polybot-proposal" in text
    assert "SupplementaryGroups=polybot" in text
    assert "ExecStartPre=/usr/bin/chgrp" not in text


def test_installer_precreates_durable_service_owned_runtime_lock():
    text = INSTALLER.read_text()

    assert 'RUNTIME_LOCK="$APP/data/shadow-runtime.lock"' in text
    assert '[ -L "$RUNTIME_LOCK" ]' in text
    assert 'install -o "$SVC_USER" -g "$SVC_USER" -m 0640 /dev/null "$RUNTIME_LOCK"' in text
    assert 'chown "$SVC_USER:$SVC_USER" "$RUNTIME_LOCK"' in text
    assert 'chmod 0640 "$RUNTIME_LOCK"' in text


def test_installer_never_recursively_reowns_preserved_data_tree():
    text = INSTALLER.read_text()
    guard = '[ -L "$APP/data" ] || [ ! -d "$APP/data" ]'
    chown = 'chown "$SVC_USER:$SVC_USER" "$APP/data"'
    chmod = 'chmod 0750 "$APP/data"'

    assert 'chown -R "$SVC_USER:$SVC_USER" "$APP/data"' not in text
    assert guard in text
    assert chown in text
    assert text.index(guard) < text.index(chown)
    assert text.index(guard) < text.index(chmod)


def test_ingestion_unit_has_fail_closed_memory_ceiling():
    text = UNIT.read_text()

    assert "MemoryAccounting=true" in text
    assert "MemoryHigh=512M" in text
    assert "MemoryMax=768M" in text
    assert "MemorySwapMax=128M" in text
    assert "OOMPolicy=stop" in text


def test_runbook_requires_nonempty_old_database_evidence():
    text = RUNBOOK.read_text()
    source_check = "test -s /opt/polymarket-bot/data/market_memory.db"
    preserved_check = 'test -s "$evidence/market_memory.db"'
    move_loop = "for path in"
    checksum = 'sha256sum -c "$evidence/SHA256SUMS"'

    assert source_check in text
    assert preserved_check in text
    assert checksum in text
    assert text.index(source_check) < text.index(move_loop)
    assert text.index(move_loop) < text.index(preserved_check) < text.index(checksum)


def test_runbook_repairs_github_origin_before_service_checkout_pull():
    text = RUNBOOK.read_text()
    set_origin = (
        "git -C /opt/polymarket-bot remote set-url origin "
        "https://github.com/jouleka/polymarket-bot.git"
    )
    assert_origin = (
        'test "$(git -C /opt/polymarket-bot remote get-url origin)" = '
        '"https://github.com/jouleka/polymarket-bot.git"'
    )
    pull = "git -C /opt/polymarket-bot pull --ff-only origin main"

    assert set_origin in text
    assert assert_origin in text
    assert pull in text
    assert text.index(set_origin) < text.index(assert_origin) < text.index(pull)
