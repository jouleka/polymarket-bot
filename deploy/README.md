# Deploy — polymarket-bot composite paper-shadow runtime

POL-18's separately gated dedicated Hermes profile, exact-five-tool preflight, stopped install,
activation, and rollback procedure is in [`hermes/README.md`](hermes/README.md). This document
continues to own the composite POL-17 ingestion/ERS runtime and production persistence contract.

Paper-only, with no keys, wallet, live order client, chain writes, or listening port. One supervised
process maintains CLOB books in memory, runs the ERS and harness against those same live books,
polls two read-only Polygon providers for resolution, persists one versioned `clob-midpoint` batch
every 60 seconds, and retains the full deduplicated Data API trade tape. It does **not** persist raw
`clob-ws` frames. Before POL-18 is separately built and attached, it idles with zero proposals.

## Current gate state

The code release gate passed on 2026-07-10: 1,800.006 seconds, 5,586,944 total DB+WAL+SHM bytes,
`{"clob-midpoint":29,"data-api":3500}`, 1,800 usable quotes, zero raw rows, all batches decoded, no HALT,
graceful close, 0.249755 GiB/day, exit 0. Independent spec review passed and the mutation battery killed 41/41.

The VPS service is installed and remains **STOPPED + DISABLED**. The separately approved first-start
gate passed on 2026-07-16 after three midpoint intervals, then POL-17 stopped gracefully. Exact
evidence is in [`../docs/VERIFICATION-POL13-FIRST-START.md`](../docs/VERIFICATION-POL13-FIRST-START.md).
All seven paper databases are preserved. First-start success does not authorize enablement or a
Hermes start; those remain separate gates.

The old raw-firehose evidence is already preserved. Never move, overwrite, rechown recursively, or
delete it. The current compact production `market_memory.db` must also remain byte-preserved until
an explicitly approved activation opens it through the compatible EventStore schema.

Short captures are diagnostic only. The 1,800-second result above is the release evidence; the 0.5 GiB/day ceiling
must not be loosened on future regressions.

## Persistence contract

Production `EventStore` sources must include:

- `clob-midpoint`: one strict, versioned batch per cadence, containing usable non-stale books;
- `data-api`: the existing full trade payload/projection with EventStore event-ID deduplication; and
- exactly zero `clob-ws` rows.

Synthetic events are not reconstructable from this compact history. They remain deferred pending a separately
designed and tuned live contract.

The composite owns seven distinct SQLite paths. No pair may resolve to the same path, symlink
target, or existing inode:

| Path | Logical owner |
|---|---|
| `data/market_memory.db` | queued midpoint/trade/news EventStore writer |
| `data/intents.db` | event-loop ERS IntentStore |
| `data/forecasts.db` | pipeline plus resolution dispatcher |
| `data/components.db` | pipeline component evidence |
| `data/maker.db` | event-loop shadow/resolution dispatchers |
| `data/shadow.db` | event-loop shadow/resolution dispatchers |
| `data/resolution.db` | one serialized resolution worker plus event-loop dispatcher |

Schema creation is forward-only and happens only when the runtime is activated. A stopped install
must not create or migrate any production database. The runtime singleton prevents a second writer.

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

## Historical raw-firehose preservation (already completed)

The following is the historical preservation procedure, retained for audit and disaster recovery.
Do not run it against the current compact production database. The raw-firehose evidence created
on 2026-07-14 must remain exactly where recorded in HANDOFF and its checksums must continue to pass:

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

Do not delete or overwrite that evidence. POL-17 does not require moving the current compact
`data/market_memory.db` or creating a fresh one.

## Install/update while remaining stopped

This section requires explicit **installation** approval. It does not authorize activation.

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

`install.sh` preserves an existing config, so the old ingestion-only file will not acquire the
required `[shadow]` section automatically. While the unit remains stopped, back it up, reconcile it
manually with `deploy/config.example.toml`, and preserve the existing ingestion values:

```sh
cp -a /opt/polymarket-bot/config.toml /opt/polymarket-bot/config.toml.pre-pol17
# Edit /opt/polymarket-bot/config.toml as root. Do not put secrets in shell history.
chmod 0640 /opt/polymarket-bot/config.toml
chown root:polybot /opt/polymarket-bot/config.toml
```

The file must explicitly keep the 60-second downsample and define all six additional distinct
database paths, the runtime status path, and exactly two independently operated read-only Polygon
HTTPS providers:

```toml
snapshot_interval_seconds = 60.0

[shadow]
intents_db_path = "/opt/polymarket-bot/data/intents.db"
forecasts_db_path = "/opt/polymarket-bot/data/forecasts.db"
components_db_path = "/opt/polymarket-bot/data/components.db"
maker_db_path = "/opt/polymarket-bot/data/maker.db"
shadow_db_path = "/opt/polymarket-bot/data/shadow.db"
resolution_db_path = "/opt/polymarket-bot/data/resolution.db"
status_path = "/run/polybot/shadow-status.json"

[[shadow.polygon_providers]]
provider_id = "provider-a"
url = "https://polygon-provider-a.example"

[[shadow.polygon_providers]]
provider_id = "provider-b"
url = "https://polygon-provider-b.example"
```

Use real approved endpoints, not the placeholders. Then validate the stopped configuration without
constructing the runtime or opening any database:

```sh
cd /opt/polymarket-bot
sudo -u polybot env PYTHONPATH=/opt/polymarket-bot/src \
  /opt/polymarket-bot/.venv/bin/python - <<'PY'
from polybot.runtime.shadow_config import load_shadow_config

c = load_shadow_config("/opt/polymarket-bot/config.toml")
assert c.ingestion.snapshot_interval_seconds == 60.0
assert len(c.database_paths) == 7
assert len(set(c.database_paths)) == 7
assert len(c.polygon_providers) == 2
print("POL-17 stopped config valid; provider IDs:",
      [p.provider_id for p in c.polygon_providers])
PY
systemctl is-enabled polymarket-ingestion.service   # disabled
systemctl is-active polymarket-ingestion.service    # inactive
```

Before the first start, this validation created no database. After any start attempt, preserve all
seven paper databases and verify the validation does not mutate or replace them. `install.sh` is
idempotent and must not start or enable the unit.

## Start/enable — separate explicit approval required

Before any activation, confirm the installed cgroup ceilings. Polymarket must never run with an
unbounded value:

```sh
systemctl show polymarket-ingestion.service \
  -p MemoryHigh -p MemoryMax -p MemorySwapMax -p OOMPolicy
# required: 536870912, 805306368, 134217728, stop
```

Only after the owner separately approves first start, start without enabling:

```sh
systemctl start polymarket-ingestion.service
journalctl -u polymarket-ingestion.service -f
```

## Verify after approved activation

```sh
systemctl status polymarket-ingestion.service
systemctl show polymarket-ingestion.service \
  -p MemoryCurrent -p MemoryPeak -p MemoryHigh -p MemoryMax -p MemorySwapCurrent -p MemorySwapMax
cat /sys/fs/cgroup/system.slice/polymarket-ingestion.service/memory.events
cat /opt/polymarket-bot/data/heartbeat
cat /run/polybot/shadow-status.json
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

The status JSON must parse, show controller state and both outbox depths, and advance atomically.
Before POL-18, `pending_intents` and `execution_outbox` must remain zero; do not inject synthetic
production proposals to make them move. Confirm the journal contains one `READY=1` transition and
no wrong-chain, registry-stale, database-integrity, or supervised-service halt.

`MemoryHigh=512M` begins reclaim before the hard `MemoryMax=768M`; swap is capped at 128 MiB.
Any `oom`/`oom_kill` event, repeated growth of the `high` counter, or peak close to the hard limit is
a failed activation gate: stop the unit and reduce universe/concurrency only through a reviewed
configuration change. Never raise the ceiling merely to keep the process alive.

Also record DB+WAL+SHM size at two timestamps to confirm observed growth remains bounded. A stale heartbeat, any raw
row, malformed midpoint batch, missing source, collector/writer HALT, or rate above the ceiling is a failure: stop
and disable the unit; do not relax the gate.

Enable only after a separately approved, clean first-start observation:

```sh
systemctl enable polymarket-ingestion.service
```

## Stop / rollback

```sh
systemctl disable --now polymarket-ingestion.service
```

This is graceful and drains the writer queue, joins the resolution worker, closes all seven stores,
and releases the singleton. Preserve every composite DB, the compact EventStore, and the old raw
evidence. Never delete a new DB to make a rollback appear clean. Restore the backed-up config and
roll code back only through the GitHub-linked service checkout; never recreate a local bare remote.

If activation fails, leave the unit stopped and disabled, capture `systemctl status`, the bounded
journal excerpt, safe file sizes/checksums, and the status JSON if present. Do not retry by relaxing
freshness, provider agreement, path separation, caps, or readiness gates.

## Deferred hardening (POL-4 / live, not paper shadow)

Egress allowlisting, `ProtectSystem=strict`, periodic backups of all compact paper stores, durable
restoration of sticky HALTED/PAUSED op-state, and moving shared Hermes off root remain deferred. No
live-money key belongs in this paper-shadow service.
