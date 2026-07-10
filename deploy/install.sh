#!/usr/bin/env bash
# Idempotent installer for the polymarket-bot ingestion runtime (read-only shadow Phase-0).
# Run as root on the VPS after checking the repo out to /opt/polymarket-bot:
#     bash /opt/polymarket-bot/deploy/install.sh
# Re-runnable: it never clobbers an existing config.toml and skips work already done.
set -euo pipefail

APP=/opt/polymarket-bot
SVC_USER=polybot
UV=/root/.local/bin/uv

echo "== 1. system user ($SVC_USER, nologin) =="
if ! id "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home "$APP" --shell /usr/sbin/nologin "$SVC_USER"
    echo "   created $SVC_USER"
else
    echo "   $SVC_USER already exists"
fi

echo "== 2. dirs =="
mkdir -p "$APP/data"

echo "== 3. venv (uv, standalone python 3.13 UNDER the app dir) + runtime deps =="
# Pin uv's python install dir under the app tree. Otherwise uv symlinks the venv to a python under
# /root/.local/share/uv (mode 0700, unreachable by the polybot service user) -> the service can't exec it.
# The app tree is root-owned + world-readable, so polybot can run .venv/bin/python.
export UV_PYTHON_INSTALL_DIR="$APP/.uv-python"
if [ ! -x "$APP/.venv/bin/python" ]; then
    "$UV" venv --python 3.13 "$APP/.venv"
fi
"$UV" pip install --python "$APP/.venv/bin/python" "httpx>=0.28" "websockets>=16"

echo "== 4. config (kept if present) =="
if [ ! -f "$APP/config.toml" ]; then
    cp "$APP/deploy/config.example.toml" "$APP/config.toml"
    echo "   installed config.toml from example"
else
    echo "   config.toml already present, kept"
fi

echo "== 5. ownership (ONLY the writable data dir -> $SVC_USER) =="
# The service only WRITES to data/. Code + venv stay root-owned + world-readable, so (a) polybot can
# still run .venv/bin/python + read src/, and (b) redeploys (`git pull` as root) never hit git's
# dubious-ownership guard. PYTHONDONTWRITEBYTECODE=1 in the unit keeps src/ write-free.
chown -R "$SVC_USER:$SVC_USER" "$APP/data"

echo "== 6. systemd unit (install only; remain stopped + disabled) =="
# Activation is a separate owner-approved action. An update must never restart or enable capture implicitly.
cp "$APP/deploy/polymarket-ingestion.service" /etc/systemd/system/polymarket-ingestion.service
systemctl daemon-reload
systemctl disable --now polymarket-ingestion.service

active_state=
if active_state=$(systemctl is-active polymarket-ingestion.service 2>&1); then :; fi
if [ "$active_state" != "inactive" ]; then
    echo "ERROR: expected polymarket-ingestion.service inactive, got: $active_state" >&2
    exit 1
fi

enabled_state=
if enabled_state=$(systemctl is-enabled polymarket-ingestion.service 2>&1); then :; fi
if [ "$enabled_state" != "disabled" ]; then
    echo "ERROR: expected polymarket-ingestion.service disabled, got: $enabled_state" >&2
    exit 1
fi

echo
echo "installed; service remains STOPPED + DISABLED"
echo "   verify config + release evidence, then follow deploy/README.md only after separate activation approval"
