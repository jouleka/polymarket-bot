#!/usr/bin/env bash
# Idempotent installer for the polymarket-bot ingestion runtime (read-only shadow Phase-0).
# Run as root on the VPS after checking the repo out to /opt/polymarket-bot:
#     bash /opt/polymarket-bot/deploy/install.sh
# Re-runnable: it never clobbers an existing config.toml and skips work already done.
set -euo pipefail

APP=/opt/polymarket-bot
SVC_USER=polybot
BRIDGE_GROUP=polybot-proposal
UV=/root/.local/bin/uv
RUNTIME_LOCK="$APP/data/shadow-runtime.lock"

verify_services_not_active() {
    local unit active_state load_state
    for unit in polymarket-ingestion.service polymarket-hermes.service; do
        active_state=
        load_state=
        if active_state=$(systemctl show --property=ActiveState --value "$unit" 2>&1); then :; fi
        if load_state=$(systemctl show --property=LoadState --value "$unit" 2>&1); then :; fi
        if [ "$unit" = "polymarket-ingestion.service" ] && \
           [ "$active_state" = "inactive" ] && [ "$load_state" = "loaded" ]; then
            continue
        fi
        if [ "$unit" = "polymarket-hermes.service" ] && \
           [ "$active_state" = "inactive" ] && \
           { [ "$load_state" = "loaded" ] || [ "$load_state" = "not-found" ]; }; then
            continue
        fi
        echo "ERROR: refusing install for unsafe $unit state: active=$active_state load=$load_state" >&2
        return 1
    done
}

verify_service_stopped_disabled() {
    local unit active_state enabled_state
    for unit in polymarket-ingestion.service polymarket-hermes.service; do
        active_state=
        if active_state=$(systemctl is-active "$unit" 2>&1); then :; fi
        if [ "$active_state" != "inactive" ]; then
            echo "ERROR: expected $unit inactive, got: $active_state" >&2
            return 1
        fi

        enabled_state=
        if enabled_state=$(systemctl is-enabled "$unit" 2>&1); then :; fi
        if [ "$enabled_state" != "disabled" ]; then
            echo "ERROR: expected $unit disabled, got: $enabled_state" >&2
            return 1
        fi
    done
}

verify_services_not_active

echo "== 1. runtime user + proposal-socket group =="
if ! id "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home "$APP" --shell /usr/sbin/nologin "$SVC_USER"
    echo "   created $SVC_USER"
else
    echo "   $SVC_USER already exists"
fi
if ! getent group "$BRIDGE_GROUP" >/dev/null 2>&1; then
    groupadd --system "$BRIDGE_GROUP"
fi
usermod -a -G "$BRIDGE_GROUP" "$SVC_USER"

echo "== 2. dirs =="
mkdir -p "$APP/data"
if [ -L "$APP/data" ] || [ ! -d "$APP/data" ]; then
    echo "ERROR: data root must be a real directory: $APP/data" >&2
    exit 1
fi

echo "== 3. venv (uv, standalone python 3.13 UNDER the app dir) + runtime deps =="
# Pin uv's python install dir under the app tree. Otherwise uv symlinks the venv to a python under
# /root/.local/share/uv (mode 0700, unreachable by the polybot service user) -> the service can't exec it.
# The app tree is root-owned + world-readable, so polybot can run .venv/bin/python.
export UV_PYTHON_INSTALL_DIR="$APP/.uv-python"
if [ ! -x "$APP/.venv/bin/python" ]; then
    "$UV" venv --python 3.13 "$APP/.venv"
fi
"$UV" pip install --python "$APP/.venv/bin/python" \
    "httpx>=0.28" "mcp==1.26.0" "websockets>=16"

echo "== 4. config (kept if present) =="
if [ ! -f "$APP/config.toml" ]; then
    cp "$APP/deploy/config.example.toml" "$APP/config.toml"
    echo "   installed config.toml from example"
else
    echo "   config.toml already present, kept"
fi

echo "== 5. ownership (ONLY the writable data directory + runtime lock -> $SVC_USER) =="
# Never recursively re-own data/: it contains preserved raw-firehose evidence and live databases.
# The service only needs the directory itself writable to create new stores; existing production
# files keep their established ownership. Code + venv stay root-owned + world-readable, so (a)
# polybot can still run .venv/bin/python + read src/, and (b) redeploys (`git pull` as root) never
# hit git's dubious-ownership guard. PYTHONDONTWRITEBYTECODE=1 keeps src/ write-free.
chown "$SVC_USER:$SVC_USER" "$APP/data"
chmod 0750 "$APP/data"
if [ -L "$RUNTIME_LOCK" ] || { [ -e "$RUNTIME_LOCK" ] && [ ! -f "$RUNTIME_LOCK" ]; }; then
    echo "ERROR: runtime lock must be a regular file: $RUNTIME_LOCK" >&2
    exit 1
fi
if [ ! -e "$RUNTIME_LOCK" ]; then
    install -o "$SVC_USER" -g "$SVC_USER" -m 0640 /dev/null "$RUNTIME_LOCK"
fi
chown "$SVC_USER:$SVC_USER" "$RUNTIME_LOCK"
chmod 0640 "$RUNTIME_LOCK"
chown root:"$SVC_USER" "$APP/config.toml"
chmod 0640 "$APP/config.toml"
if [ -f "$APP/.env" ]; then
    chown root:"$SVC_USER" "$APP/.env"
    chmod 0640 "$APP/.env"
fi

echo "== 6. systemd units (install only; remain stopped + disabled) =="
# Activation is a separate owner-approved action. An update must never restart or enable capture implicitly.
cp "$APP/deploy/polymarket-ingestion.service" /etc/systemd/system/polymarket-ingestion.service
cp "$APP/deploy/polymarket-hermes.service" /etc/systemd/system/polymarket-hermes.service
systemctl daemon-reload
systemctl disable --now polymarket-ingestion.service polymarket-hermes.service
verify_service_stopped_disabled

echo
echo "installed; both services remain STOPPED + DISABLED; Hermes profile was not created"
echo "   verify config + release evidence, then follow deploy/README.md only after separate activation approval"
