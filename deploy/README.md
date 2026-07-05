# Deploy — polymarket-bot ingestion runtime (Phase-0 shadow)

Read-only, no keys. Runs the S1 ingestion collectors continuously as an isolated systemd service on the VPS
(`srv1779077`), capturing the **un-backfillable** order-book + trade stream into a durable SQLite `EventStore`.
Fully isolated from the co-tenant bots (memecoin-bot, the shared root Hermes): dedicated `polybot` user, own
`/opt/polymarket-bot` tree, own `polymarket-ingestion.service`, own bare repo. No listening port (outbound-only).

## Layout on the VPS
```
/opt/polymarket-bot/            # checkout of this repo (owned by polybot)
  .venv/                        # uv-managed, standalone cpython 3.13 (deps: httpx, websockets)
  src/                          # PYTHONPATH root
  config.toml                   # prod config (copied from deploy/config.example.toml)
  data/market_memory.db         # THE un-backfillable store (SQLite/WAL)
  data/heartbeat                # liveness file
  deploy/                       # this kit
/root/git/polymarket-bot.git    # bare repo (push target from the operator's WSL)
/etc/systemd/system/polymarket-ingestion.service
```

## First deploy
On the **VPS** (root):
```sh
git init --bare /root/git/polymarket-bot.git
```
On the operator's **WSL** (has the repo + GitHub + VPS SSH):
```sh
cd ~/projects/polymarket-bot
git remote add polybot-vps root@100.111.199.109:/root/git/polymarket-bot.git   # once
git push polybot-vps main
```
Back on the **VPS** (root):
```sh
git clone /root/git/polymarket-bot.git /opt/polymarket-bot
bash /opt/polymarket-bot/deploy/install.sh      # user, venv+deps, config, systemd unit (idempotent)
systemctl start polymarket-ingestion.service
journalctl -u polymarket-ingestion.service -f   # watch it discover the universe + start streaming
ls -l /opt/polymarket-bot/data/                 # market_memory.db + heartbeat appear and grow
```

## Redeploy (after new commits)
WSL: `git push polybot-vps main`
VPS: `cd /opt/polymarket-bot && git pull && bash deploy/install.sh && systemctl restart polymarket-ingestion.service`

## Verify it's healthy
- `systemctl status polymarket-ingestion.service` — active (running).
- Row count grows: `/opt/polymarket-bot/.venv/bin/python -c "import sqlite3;print(sqlite3.connect('/opt/polymarket-bot/data/market_memory.db').execute('select count(*) from events').fetchone()[0])"`
- Heartbeat fresh: `cat /opt/polymarket-bot/data/heartbeat` (counter increments every ~5s).

## Stop / rollback
- Stop (graceful, drains the writer queue): `systemctl stop polymarket-ingestion.service`
- Disable: `systemctl disable --now polymarket-ingestion.service`
- The `data/` store persists across restarts and redeploys.

## Deferred hardening (POL-4 / live, not Phase-0)
Egress allowlist (only gamma/clob-ws/data-api/polygon), `ProtectSystem=strict`, a periodic backup of the
un-backfillable `market_memory.db`, and moving the shared Hermes off root into its own unprivileged user.
