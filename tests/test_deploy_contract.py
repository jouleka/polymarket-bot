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
    reload = "systemctl daemon-reload"
    disable = "systemctl disable --now polymarket-ingestion.service"
    verify = "verify_service_stopped_disabled"

    assert install in commands
    assert reload in commands
    assert disable in commands
    assert verify in commands
    assert commands.index(install) < commands.index(reload) < commands.index(disable) < commands.index(verify)
    assert "systemctl is-active polymarket-ingestion.service" in text
    assert "systemctl is-enabled polymarket-ingestion.service" in text
    assert all("|| true" not in line for line in commands if "disable --now" in line)
    assert not re.search(r"^\s*systemctl\s+(?:enable|reenable|start|restart)\b", text, re.MULTILINE)
    assert "installed; service remains STOPPED + DISABLED" in text


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
