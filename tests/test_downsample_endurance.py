from pathlib import Path

import pytest

from scripts.downsample_endurance_check import footprint, projected_gib_per_day


GIB = 1024 ** 3
SECONDS_PER_DAY = 86400


def test_projected_gib_per_day_exact_known_rate():
    assert projected_gib_per_day(GIB, SECONDS_PER_DAY) == 1.0
    assert projected_gib_per_day(GIB // 2, SECONDS_PER_DAY * 2) == 0.25


@pytest.mark.parametrize("elapsed_seconds", [0, -1.0])
def test_projected_gib_per_day_rejects_nonpositive_elapsed(elapsed_seconds):
    with pytest.raises(ValueError, match="elapsed_seconds"):
        projected_gib_per_day(1, elapsed_seconds)


def test_footprint_includes_db_and_wal_and_ignores_missing_shm(tmp_path):
    db = tmp_path / "capture.db"
    wal = Path(f"{db}-wal")
    shm = Path(f"{db}-shm")
    db.write_bytes(b"db!")
    wal.write_bytes(b"wal!!")

    assert footprint([db, wal, shm]) == 8
