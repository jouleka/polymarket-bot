from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "deploy" / "install.sh"
UNIT = ROOT / "deploy" / "polymarket-ingestion.service"


def test_installer_leaves_service_stopped_and_disabled():
    text = INSTALLER.read_text()
    assert "systemctl enable polymarket-ingestion.service" not in text
    assert "systemctl start polymarket-ingestion.service" not in text
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
