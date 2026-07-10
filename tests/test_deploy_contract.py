import re
from pathlib import Path


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

    assert install in commands
    assert reload in commands
    assert disable in commands
    assert commands.index(install) < commands.index(reload) < commands.index(disable)
    assert "systemctl is-active polymarket-ingestion.service" in text
    assert "systemctl is-enabled polymarket-ingestion.service" in text
    assert all("|| true" not in line for line in commands if "disable --now" in line)
    assert not re.search(r"^\s*systemctl\s+(?:enable|reenable|start|restart)\b", text, re.MULTILINE)
    assert "installed; service remains STOPPED + DISABLED" in text


def test_unit_describes_compact_midpoint_and_trade_persistence():
    description = next(
        line for line in UNIT.read_text().splitlines()
        if line.startswith("Description=")
    )
    assert "midpoint" in description
    assert "trade" in description
    assert "raw" not in description
    assert "un-backfillable order-book" not in description


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
