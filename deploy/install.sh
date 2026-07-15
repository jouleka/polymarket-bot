#!/usr/bin/env bash
# Idempotent installer for the polymarket-bot ingestion runtime (read-only shadow Phase-0).
# Run as root on the VPS after checking the repo out to /opt/polymarket-bot:
#     bash /opt/polymarket-bot/deploy/install.sh
# Re-runnable: it never clobbers an existing config.toml and skips work already done.
set -euo pipefail

APP=/opt/polymarket-bot
SVC_USER=polybot
BRAIN_USER=polybot-hermes
BRAIN_HOME=/var/lib/polybot-hermes
BRIDGE_GROUP=polybot-proposal
UV=/root/.local/bin/uv

verify_services_not_active() {
    local unit active_state
    for unit in polymarket-ingestion.service polymarket-hermes.service; do
        active_state=
        if active_state=$(systemctl is-active "$unit" 2>&1); then :; fi
        if [ "$active_state" != "inactive" ]; then
            echo "ERROR: refusing install unless $unit is exactly inactive; got: $active_state" >&2
            return 1
        fi
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

echo "== 1. isolated users + proposal-socket group =="
if ! id "$SVC_USER" >/dev/null 2>&1; then
    useradd --system --home "$APP" --shell /usr/sbin/nologin "$SVC_USER"
    echo "   created $SVC_USER"
else
    echo "   $SVC_USER already exists"
fi
if ! getent group "$BRIDGE_GROUP" >/dev/null 2>&1; then
    groupadd --system "$BRIDGE_GROUP"
fi
if ! id "$BRAIN_USER" >/dev/null 2>&1; then
    useradd --system --create-home --home-dir "$BRAIN_HOME" \
        --shell /usr/sbin/nologin "$BRAIN_USER"
else
    brain_passwd=$(getent passwd "$BRAIN_USER")
    brain_uid=$(id -u "$BRAIN_USER")
    if [ "$(printf '%s' "$brain_passwd" | cut -d: -f6)" != "$BRAIN_HOME" ] || \
       [ "$(printf '%s' "$brain_passwd" | cut -d: -f7)" != /usr/sbin/nologin ] || \
       [ "$brain_uid" -ge 1000 ]; then
        echo "ERROR: existing $BRAIN_USER identity violates the isolated system-user contract" >&2
        exit 1
    fi
fi
usermod -a -G "$BRIDGE_GROUP" "$SVC_USER"
usermod -a -G "$BRIDGE_GROUP" "$BRAIN_USER"
if id -nG "$BRAIN_USER" | tr ' ' '\n' | grep -Fx "$SVC_USER" >/dev/null; then
    echo "ERROR: $BRAIN_USER must not belong to database/config group $SVC_USER" >&2
    exit 1
fi
expected_brain_groups=$(printf '%s\n%s\n' "$BRAIN_USER" "$BRIDGE_GROUP" | sort)
actual_brain_groups=$(id -nG "$BRAIN_USER" | tr ' ' '\n' | sort)
if [ "$actual_brain_groups" != "$expected_brain_groups" ]; then
    echo "ERROR: $BRAIN_USER has supplementary authority outside the socket-only contract" >&2
    exit 1
fi

echo "== 2. dirs =="
mkdir -p "$APP/data"
install -d -m 0700 -o "$BRAIN_USER" -g "$BRAIN_USER" "$BRAIN_HOME"

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

echo "== 5. ownership (ONLY the writable data dir -> $SVC_USER) =="
# The service only WRITES to data/. Code + venv stay root-owned + world-readable, so (a) polybot can
# still run .venv/bin/python + read src/, and (b) redeploys (`git pull` as root) never hit git's
# dubious-ownership guard. PYTHONDONTWRITEBYTECODE=1 in the unit keeps src/ write-free.
chown -R "$SVC_USER:$SVC_USER" "$APP/data"
chmod 0750 "$APP/data"
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
