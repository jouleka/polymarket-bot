# POL-18 stopped deployment and activation runbook

This runbook installs the propose-only Hermes brain as a normal named profile in the existing
root-owned Hermes installation beside the composite POL-17 paper
runtime. It never grants wallet/trading keys, database access, a shell, or tools beyond the exact
five-tool MCP surface. The existing root-owned default, coder, memecoin, and optionsbot profiles
must not be cloned, edited, stopped, or restarted.

Code/unit installation, profile creation/model selection, cron creation, POL-17 activation,
POL-18 activation, and enablement are separate owner gates. Running one section does not authorize
the next. The repository build and tests do not perform any of these operations.

## 1. Code and unit installation while both services remain stopped

Requires explicit stopped-installation approval. The installer validates the existing `polybot`
runtime user and creates only the socket group if absent. It does not install another Hermes copy,
create another Hermes home, or create a second Hermes user:

```sh
systemctl disable --now polymarket-hermes.service polymarket-ingestion.service
git -C /opt/polymarket-bot pull --ff-only origin main
bash /opt/polymarket-bot/deploy/install.sh
systemctl is-active polymarket-ingestion.service   # inactive
systemctl is-enabled polymarket-ingestion.service # disabled
systemctl is-active polymarket-hermes.service     # inactive
systemctl is-enabled polymarket-hermes.service    # disabled
```

It does not create a Hermes profile, write cron state, start a gateway, or open production
databases. Verify the host boundary:

```sh
getent passwd polybot
getent group polybot-proposal
id -nG polybot
```

The `polybot` service belongs to `polybot-proposal`, creates `/run/polybot-proposal`, changes only
that runtime directory/socket to the shared group, and publishes the socket at mode `0660`. The
Hermes unit joins that socket group through systemd while retaining the existing root Hermes
profile/auth scope. Its systemd sandbox hides production config/data, root SSH/Codex/config homes,
and every unrelated named profile.

Reconcile the stopped composite config with `deploy/config.example.toml`. Preserve all existing
POL-17 database paths and provider values, and add exactly:

```toml
proposal_socket_path = "/run/polybot-proposal/proposal.sock"
proposal_socket_group = "polybot-proposal"
proposal_max_per_minute = 20
proposal_request_timeout_seconds = 2.0
```

Loading configuration must not construct the runtime or create a database:

```sh
sudo -u polybot env PYTHONPATH=/opt/polymarket-bot/src \
  /opt/polymarket-bot/.venv/bin/python - <<'PY'
from polybot.runtime.shadow_config import load_shadow_config
c = load_shadow_config("/opt/polymarket-bot/config.toml")
assert c.proposal_socket_path == "/run/polybot-proposal/proposal.sock"
assert c.proposal_socket_group == "polybot-proposal"
assert c.proposal_max_per_minute == 20
print("POL-18 stopped composite config: PASS")
PY
```

## 2. Native profile creation and model selection while stopped

Requires separate profile-installation approval. Do not use `--clone`, `--clone-all`, or
`--clone-from`:

```sh
/usr/local/bin/hermes profile create polymarket \
  --no-alias --no-skills \
  --description "Isolated propose-only Polymarket paper analyst"

PROFILE=/root/.hermes/profiles/polymarket
install -o root -g root -m 0600 \
  /opt/polymarket-bot/deploy/hermes/polymarket-profile/config.yaml \
  "$PROFILE/config.yaml"
install -o root -g root -m 0600 \
  /opt/polymarket-bot/deploy/hermes/polymarket-profile/SOUL.md \
  "$PROFILE/SOUL.md"
test ! -e "$PROFILE/.env"
systemctl is-active polymarket-hermes.service   # inactive
systemctl is-enabled polymarket-hermes.service # disabled
```

Profile creation must produce its own memory, sessions, skills, and cron directories under
`/root/.hermes/profiles/polymarket`. It must not clone or modify another profile. Compare the
existing profile list/state before and after and stop if any unrelated profile changed.

The reviewed production selection is `gpt-5.6-terra`, provider `openai-codex`, base URL
`https://chatgpt.com/backend-api/codex`, and reasoning effort `high`.

## 3. Existing Hermes authentication proof while stopped

Native named profiles inherit the existing root Hermes provider store when they have no local
provider override. Do not run another device login and do not copy tokens into the profile. Verify
only provider names/credential counts—never token values:

```sh
/usr/local/bin/hermes --profile polymarket auth list
test ! -e /root/.hermes/profiles/polymarket/auth.json
test ! -e /root/.hermes/profiles/polymarket/.env
test ! -e /root/.hermes/profiles/polymarket/.op.env
```

The output must include the existing `openai-codex` credential. A model credential is not a
wallet/trading key and must never be copied into POL-17 config, source, prompt, SOUL, skill, memory,
or cron text.

The 2026-07-16 failed enablement created one forbidden profile-local `auth.json` through Hermes's
unselected Nous keepalive. This is a one-time incident cleanup, not a general instruction to delete
an unexpected credential file. Only while both units are inactive and disabled, after confirming
the file is the recorded root-owned, mode-0600 regular file and recording non-secret metadata plus
its checksum, remove that generated copy. Do not read or print its contents, and do not touch the
native root auth store:

```sh
test "$(systemctl is-active polymarket-ingestion.service)" = inactive
test "$(systemctl is-active polymarket-hermes.service)" = inactive
test "$(systemctl is-enabled polymarket-ingestion.service)" = disabled
test "$(systemctl is-enabled polymarket-hermes.service)" = disabled
test -f /root/.hermes/profiles/polymarket/auth.json
test ! -L /root/.hermes/profiles/polymarket/auth.json
test "$(stat -c '%U:%G %a' /root/.hermes/profiles/polymarket/auth.json)" = "root:root 600"
stat -c 'size=%s birth=%w modify=%y inode=%i' \
  /root/.hermes/profiles/polymarket/auth.json
sha256sum /root/.hermes/profiles/polymarket/auth.json
unlink -- /root/.hermes/profiles/polymarket/auth.json
test ! -e /root/.hermes/profiles/polymarket/auth.json
test -f /root/.hermes/auth.json
```

If any identity check differs, stop for owner review instead of deleting it. The bootstrap guard
below must be installed before this cleanup, so the same unselected maintenance path cannot simply
recreate the file on retry.

## 4. Exact effective-inventory preflight while stopped

The preflight runs in the pinned Hermes 0.18.2 environment, starts only the local stdio MCP bridge
for tool discovery, and does not need POL-17 or its socket to be active:

The application and native Hermes environments must both carry the reviewed MCP SDK version. On
an update from MCP 1.26.0, stop both services first, run the ordinary application installer, and
upgrade only the SDK dependency in the existing Hermes environment—do not recreate Hermes or any
profile:

```sh
/root/.local/bin/uv pip install \
  --python /usr/local/lib/hermes-agent/venv/bin/python "mcp==1.28.1"
/opt/polymarket-bot/.venv/bin/python -c \
  'import importlib.metadata as m; assert m.version("mcp") == "1.28.1"'
/usr/local/lib/hermes-agent/venv/bin/python -c \
  'import importlib.metadata as m; assert m.version("mcp") == "1.28.1"'
```

Any dependency resolver change beyond the single MCP replacement is a stop condition. Then run
the exact effective-inventory preflight. The command below is for a new pre-cron profile; for the
existing approved production cron, omit `--expect-no-cron` and run the full stopped verification
in section 5:

```sh
setpriv --reuid=0 --regid=0 --groups="$(getent group polybot-proposal | cut -d: -f3)" env \
  HOME=/root \
  PYTHONPATH=/opt/polymarket-bot/src \
  /usr/local/lib/hermes-agent/venv/bin/python \
  -m polybot.hermes.profile_verify \
  --profile-home /root/.hermes/profiles/polymarket \
  --expect-no-cron
```

Required output ends with `exact five; PASS`. Any version mismatch, extra/missing MCP tool,
resource/prompt capability, native/plugin toolset, second MCP server, unsafe command/env, or model
placeholder is a hard failure. The authored per-platform lists must be empty (Hermes's explicit
no-native-tools selection), while the effective resolver must layer back exactly the sole
`polymarket` MCP server. Every pinned built-in/plugin messaging adapter must be explicitly disabled,
the effective gateway inventory must contain zero enabled adapters, and
`kanban.dispatch_in_gateway` must be false. Profile-local auth/env files are forbidden. The unit's
launcher scrubs inherited authority variables and its sandbox hides root/project/managed Hermes
config and environment files while retaining only the native root provider `auth.json`. Do not
bypass the launcher or remove the service's identical `ExecStartPre`.

The reviewed launcher enters `polybot.hermes.profile_bootstrap` before importing Hermes. Hermes
0.18.2 otherwise starts an unselected global Nous credential keepalive after 60 seconds and can
copy that provider into the named profile even when `openai-codex` is selected. The bootstrap
disables only that maintenance starter; removing or bypassing it is an auth-isolation failure. The
profile-local `auth.json`, `.env`, and `.op.env` must remain absent before start and after every live
observation. A run that reaches the 60-second boundary and creates any of them fails closed: stop
and disable both units, preserve non-secret metadata as evidence, and do not relax preflight.

## 5. Cron creation while the gateway remains stopped

Requires separate cron-state approval. Use the reviewed prompt verbatim and do not attach skills,
scripts, workdirs, delivery platforms, or additional tools:

```sh
env \
  HOME=/root \
  HERMES_HOME=/root/.hermes/profiles/polymarket \
  /usr/local/lib/hermes-agent/venv/bin/python - <<'PY'
from pathlib import Path
from cron.jobs import create_job

prompt = Path(
    "/opt/polymarket-bot/deploy/hermes/polymarket-profile/cron-prompt.md"
).read_text(encoding="utf-8").rstrip("\n")
create_job(
    prompt=prompt,
    schedule="every 5m",
    name="polymarket-propose-only",
    deliver="local",
    skills=[],
    enabled_toolsets=["polymarket"],
)
PY
/usr/local/bin/hermes --profile polymarket cron list
setpriv --reuid=0 --regid=0 --groups="$(getent group polybot-proposal | cut -d: -f3)" env \
  HOME=/root \
  PYTHONPATH=/opt/polymarket-bot/src \
  /usr/local/lib/hermes-agent/venv/bin/python \
  -m polybot.hermes.profile_verify \
  --profile-home /root/.hermes/profiles/polymarket
systemctl is-active polymarket-hermes.service   # inactive
systemctl is-enabled polymarket-hermes.service # disabled
```

There must be exactly one such job. Do not run it manually to manufacture a production proposal.
Automatic cron execution begins only when the profile gateway is separately activated.

## 6. Separately approved activation

POL-17 activation and POL-18 activation are distinct approvals. Start without enabling first:

```sh
systemctl show polymarket-ingestion.service \
  -p MemoryHigh -p MemoryMax -p MemorySwapMax -p OOMPolicy
systemctl show polymarket-hermes.service \
  -p MemoryHigh -p MemoryMax -p MemorySwapMax -p OOMPolicy
# required hard ceilings: ingestion 768 MiB; Hermes 512 MiB; swap 128 MiB each

systemctl start polymarket-ingestion.service
systemctl is-active polymarket-ingestion.service
stat -c '%A %U %G %n' /run/polybot-proposal /run/polybot-proposal/proposal.sock
cat /run/polybot/shadow-status.json

systemctl start polymarket-hermes.service
systemctl is-active polymarket-hermes.service
journalctl -u polymarket-hermes.service --since=-5m --no-pager
systemctl show polymarket-ingestion.service polymarket-hermes.service \
  -p MemoryCurrent -p MemoryPeak -p MemoryHigh -p MemoryMax \
  -p MemorySwapCurrent -p MemorySwapMax
cat /sys/fs/cgroup/system.slice/polymarket-ingestion.service/memory.events
cat /sys/fs/cgroup/system.slice/polymarket-hermes.service/memory.events
```

`Requisite=` makes a brain start fail unless POL-17 is already active and does not pull-start it;
`PartOf=` propagates explicit POL-17 stop/restart operations. An unexpected POL-17 crash can leave
the Hermes gateway process present while systemd restarts POL-17, but the missing proposal socket
makes every affected brain run fail closed with zero fallback authority.

Required before leaving it running:

- POL-17 reports ready with healthy registry/books and zero raw `clob-ws` persistence;
- runtime directory is `polybot:polybot-proposal` and socket is `polybot:polybot-proposal 0660`;
- brain preflight reports exact five and the journal contains no terminal/file/browser/web/plugin
  tool, extra MCP server, profile migration, or credential/config error;
- the journal reports no attempted messaging-platform connection, token collision, invalid
  toolset warning, kanban database open/dispatcher error, or system-wide profile interaction;
- the first cron run either emits no proposal or at most one genuine evidence-backed `PROPOSED`
  row; no fake proposal is injected for testing;
- any proposal is independently processed by ERS with fresh-book re-fetch, PaperSigner only, and
  the existing atomic/restart-safe shadow path;
- both cgroups stay below their soft ceilings without `oom`/`oom_kill` events or a repeatedly
  increasing `high` counter. Stop on memory pressure; never raise limits merely to keep them alive.

Enable either unit only after a further explicit enablement approval:

```sh
systemctl enable polymarket-ingestion.service
systemctl enable polymarket-hermes.service
```

## 7. Stop and rollback

```sh
systemctl disable --now polymarket-hermes.service
systemctl disable --now polymarket-ingestion.service
```

The Hermes unit's `ExecStop` must write the profile-scoped native planned-stop marker before it
signals the gateway, then wait for that exact PID/start-time identity to exit before returning.
After a deliberate stop, require `Result=success`, `NRestarts=0`, no surviving `polymarket` gateway
PID, and both units disabled:

```sh
systemctl show polymarket-hermes.service \
  -p ActiveState -p SubState -p Result -p NRestarts -p UnitFileState
```

Preserve the Hermes profile, cron state, every paper database/WAL/SHM, compact midpoint/trade/news
history, terminal receipts, outboxes, and historical raw-firehose evidence. Never delete data or
recreate `/root/git/polymarket-bot.git` to make rollback look clean. Roll code back only through the
GitHub-linked service checkout. If the brain fails, POL-17 can remain safely paper-idle; never give
the brain SQLite access, a persisted-midpoint fallback, another collector, broader tools, or
membership in `polybot` to work around the failure.
