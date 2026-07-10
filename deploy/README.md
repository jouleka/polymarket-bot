# Deploy — polymarket-bot ingestion runtime (Phase-0 shadow)

Read-only, no keys, intents, orders, or listening port. The corrected runtime maintains CLOB books in memory,
persists one versioned `clob-midpoint` batch every 60 seconds, and retains the full deduplicated Data API trade tape.
It does **not** persist raw `clob-ws` frames.

## Current gate state

The VPS service is **STOPPED + DISABLED**. Do not install, start, enable, or restart the corrected build until:

1. the feature branch is reviewed and merged;
2. the 1800-second public-data gate passes at total DB+WAL+SHM `<= 0.5 GiB/day`;
3. push approval and deployment approval are granted separately; and
4. the old raw database is preserved and verified as described below.

Short 70-second captures are smoke tests, not release evidence. The 0.5 GiB/day ceiling must not be loosened if
the real gate fails.

## Persistence contract

Production `EventStore` sources must include:

- `clob-midpoint`: one strict, versioned batch per cadence, containing usable non-stale books;
- `data-api`: the existing full trade payload/projection with EventStore event-ID deduplication; and
- exactly zero `clob-ws` rows.

Synthetic events are not reconstructable from this compact history. They remain deferred pending a separately
designed and tuned live contract.

## GitHub-authoritative VPS layout

Do not create or use `/root/git/polymarket-bot.git`. Both checkouts must point to GitHub:

```
/root/projects/polymarket-bot/   # root's GitHub-linked maintenance checkout
/opt/polymarket-bot/             # root-owned, world-readable service checkout; executed as polybot
  .venv/                         # standalone CPython 3.13 under the app tree
  config.toml                    # copied once from deploy/config.example.toml
  data/                          # only service-writable directory
  src/
  deploy/
/etc/systemd/system/polymarket-ingestion.service
```

For an initial checkout, after GitHub access is configured without embedding credentials in a command or remote:

```sh
git clone https://github.com/jouleka/polymarket-bot.git /root/projects/polymarket-bot
git clone https://github.com/jouleka/polymarket-bot.git /opt/polymarket-bot
git -C /root/projects/polymarket-bot remote get-url origin
git -C /opt/polymarket-bot remote get-url origin
```

Both commands must print `https://github.com/jouleka/polymarket-bot.git` (or the equivalent GitHub SSH URL).
Never substitute a local bare remote.

## Pre-deployment release gate

Run from the reviewed checkout, without systemd:

```sh
./.venv/bin/python scripts/downsample_endurance_check.py \
  --seconds 1800 \
  --universe-max-markets 200 \
  --max-gib-per-day 0.5
```

Required: exit 0, midpoint and trade rows, usable quotes, zero raw rows, all midpoint batches decodable, no HALT,
graceful close, and projected total footprint at or below the ceiling.

## Preserve the old raw database

Only after explicit deployment approval, stop and disable first. A graceful stop checkpoints and closes SQLite.
Preserve the DB and any sidecars; record byte sizes and SHA-256 checksums without printing secrets:

```sh
systemctl disable --now polymarket-ingestion.service
test -s /opt/polymarket-bot/data/market_memory.db
stamp=$(date -u +%Y%m%dT%H%M%SZ)
evidence=/opt/polymarket-bot/data/raw-firehose-${stamp}
mkdir -p "$evidence"
for path in \
  /opt/polymarket-bot/data/market_memory.db \
  /opt/polymarket-bot/data/market_memory.db-wal \
  /opt/polymarket-bot/data/market_memory.db-shm
do
  if [ -e "$path" ]; then mv "$path" "$evidence/"; fi
done
test -s "$evidence/market_memory.db"
find "$evidence" -maxdepth 1 -type f -printf '%f %s bytes\n' | sort >"$evidence/SIZES.txt"
find "$evidence" -maxdepth 1 -type f ! -name 'SHA256SUMS' -print0 \
  | sort -z | xargs -0 sha256sum >"$evidence/SHA256SUMS"
sha256sum -c "$evidence/SHA256SUMS"
test ! -e /opt/polymarket-bot/data/market_memory.db
```

Do not delete or overwrite this evidence. The corrected service must start with a fresh
`data/market_memory.db`.

## Install/update while remaining stopped

```sh
systemctl disable --now polymarket-ingestion.service
git -C /root/projects/polymarket-bot remote set-url origin https://github.com/jouleka/polymarket-bot.git
git -C /opt/polymarket-bot remote set-url origin https://github.com/jouleka/polymarket-bot.git
test "$(git -C /root/projects/polymarket-bot remote get-url origin)" = "https://github.com/jouleka/polymarket-bot.git"
test "$(git -C /opt/polymarket-bot remote get-url origin)" = "https://github.com/jouleka/polymarket-bot.git"
git -C /root/projects/polymarket-bot pull --ff-only origin main
git -C /opt/polymarket-bot pull --ff-only origin main
bash /opt/polymarket-bot/deploy/install.sh
systemctl is-enabled polymarket-ingestion.service   # required: disabled
systemctl is-active polymarket-ingestion.service    # required: inactive
```

Confirm `/opt/polymarket-bot/config.toml` explicitly contains:

```toml
snapshot_interval_seconds = 60.0
```

`install.sh` is idempotent, preserves an existing config, and must not start or enable the unit.

## Start/enable — separate explicit approval required

Only after the owner separately approves activation:

```sh
systemctl enable --now polymarket-ingestion.service
journalctl -u polymarket-ingestion.service -f
```

## Verify after approved activation

```sh
systemctl status polymarket-ingestion.service
cat /opt/polymarket-bot/data/heartbeat
sudo -u polybot /opt/polymarket-bot/.venv/bin/python - <<'PY'
import sqlite3

path = "/opt/polymarket-bot/data/market_memory.db"
with sqlite3.connect(path) as db:
    counts = dict(db.execute(
        "SELECT source, COUNT(*) FROM events GROUP BY source ORDER BY source"
    ))
print(counts)
assert counts.get("clob-midpoint", 0) > 0
assert counts.get("data-api", 0) > 0
assert counts.get("clob-ws", 0) == 0
PY
```

Also record DB+WAL+SHM size at two timestamps to confirm observed growth remains bounded. A stale heartbeat, any raw
row, malformed midpoint batch, missing source, collector/writer HALT, or rate above the ceiling is a failure: stop
and disable the unit; do not relax the gate.

## Stop / rollback

```sh
systemctl disable --now polymarket-ingestion.service
```

This is graceful and drains the writer queue. Preserve both the corrected DB and the old raw evidence. Roll code
back only through the GitHub-linked service checkout; never recreate a local bare deployment remote.

## Deferred hardening (POL-4 / live, not Phase-0)

Egress allowlisting, `ProtectSystem=strict`, periodic backups of the compact midpoint+trade store, and moving shared
Hermes off root remain deferred. No live-money key belongs in this read-only ingestion service.
