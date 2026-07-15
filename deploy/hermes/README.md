# POL-18 stopped deployment and activation runbook

This runbook installs the dedicated propose-only Hermes brain beside the composite POL-17 paper
runtime. It never grants wallet/trading keys, database access, a shell, or tools beyond the exact
five-tool MCP surface. The existing root-owned default, coder, memecoin, and optionsbot profiles
must not be cloned, edited, stopped, or restarted.

Code/identity installation, profile creation, model authentication, cron creation, POL-17 activation,
POL-18 activation, and enablement are separate owner gates. Running one section does not authorize
the next. The repository build and tests do not perform any of these operations.

## 1. Code and isolated-identity installation while both services remain stopped

Requires explicit code/identity-installation approval. This single stopped-host gate includes
creation or validation of the two nologin users and socket-only group; it is never run under the
reviewed-build approval alone:

```sh
systemctl disable --now polymarket-hermes.service polymarket-ingestion.service
git -C /opt/polymarket-bot pull --ff-only origin main
bash /opt/polymarket-bot/deploy/install.sh
systemctl is-active polymarket-ingestion.service   # inactive
systemctl is-enabled polymarket-ingestion.service # disabled
systemctl is-active polymarket-hermes.service     # inactive
systemctl is-enabled polymarket-hermes.service    # disabled
```

The installer creates only the two nologin identities and the socket-only shared group. It does
not create a Hermes profile, write cron state, start a gateway, or open production databases.
Verify the host boundary:

```sh
getent passwd polybot polybot-hermes
getent group polybot-proposal
id -nG polybot
id -nG polybot-hermes
test "$(getent passwd polybot-hermes | cut -d: -f7)" = /usr/sbin/nologin
! id -nG polybot-hermes | tr ' ' '\n' | grep -Fx polybot
! sudo -u polybot-hermes test -r /opt/polymarket-bot/config.toml
! sudo -u polybot-hermes test -r /opt/polymarket-bot/.env
! sudo -u polybot-hermes test -x /opt/polymarket-bot/data
```

Both users must belong to `polybot-proposal`. `polybot-hermes` must not belong to `polybot`; it
must not be able to read `/opt/polymarket-bot/config.toml` or any database under
`/opt/polymarket-bot/data`. The `polybot` service creates `/run/polybot-proposal`, changes only that
runtime directory/socket to the shared group, and publishes the socket at mode `0660`.

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

## 2. Isolated profile creation while stopped

Requires separate profile-installation approval. Do not use `--clone`, `--clone-all`, or
`--clone-from`:

```sh
sudo -u polybot-hermes env HOME=/var/lib/polybot-hermes \
  /usr/local/bin/hermes profile create polymarket \
  --no-alias --no-skills \
  --description "Isolated propose-only Polymarket paper analyst"

PROFILE=/var/lib/polybot-hermes/.hermes/profiles/polymarket
install -o polybot-hermes -g polybot-hermes -m 0600 \
  /opt/polymarket-bot/deploy/hermes/polymarket-profile/config.yaml \
  "$PROFILE/config.yaml"
install -o polybot-hermes -g polybot-hermes -m 0600 \
  /opt/polymarket-bot/deploy/hermes/polymarket-profile/SOUL.md \
  "$PROFILE/SOUL.md"
test ! -e "$PROFILE/.env"
systemctl is-active polymarket-hermes.service   # inactive
systemctl is-enabled polymarket-hermes.service # disabled
```

Profile creation must produce its own memory, sessions, skills, and cron directories under the
dedicated home. It must not create files under `/root/.hermes` or another profile. Compare the
existing profile list/state before and after and stop if any unrelated profile changed.

## 3. Owner-selected model authentication while stopped

The template deliberately contains `OWNER_CONFIG_REQUIRED`; preflight refuses it. This gate
requires an owner-approved model/provider and its isolated model credential. A model credential is
not a wallet/trading key and must never be copied into POL-17's `.env`, config, source, prompt,
SOUL, skill, memory, or cron text.

Run Hermes model setup explicitly as `polybot-hermes` with `HOME=/var/lib/polybot-hermes`, select
the approved model, then inspect only non-secret config fields. Do not clone auth from root or any
other profile. Confirm that the profile still contains exactly one MCP server and no messaging
platform tokens.

## 4. Exact effective-inventory preflight while stopped

The preflight runs in the pinned Hermes 0.18.2 environment, starts only the local stdio MCP bridge
for tool discovery, and does not need POL-17 or its socket to be active:

```sh
sudo -u polybot-hermes env \
  HOME=/var/lib/polybot-hermes \
  PYTHONPATH=/opt/polymarket-bot/src \
  /usr/local/lib/hermes-agent/venv/bin/python \
  -m polybot.hermes.profile_verify \
  --profile-home /var/lib/polybot-hermes/.hermes/profiles/polymarket \
  --expect-no-cron
```

Required output ends with `exact five; PASS`. Any version mismatch, extra/missing MCP tool,
resource/prompt capability, native/plugin toolset, second MCP server, unsafe command/env, or model
placeholder is a hard failure. Do not bypass or remove the service's identical `ExecStartPre`.

## 5. Cron creation while the gateway remains stopped

Requires separate cron-state approval. Use the reviewed prompt verbatim and do not attach skills,
scripts, workdirs, delivery platforms, or additional tools:

```sh
sudo -u polybot-hermes env \
  HOME=/var/lib/polybot-hermes \
  HERMES_HOME=/var/lib/polybot-hermes/.hermes/profiles/polymarket \
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
sudo -u polybot-hermes env HOME=/var/lib/polybot-hermes \
  /usr/local/bin/hermes --profile polymarket cron list
sudo -u polybot-hermes env \
  HOME=/var/lib/polybot-hermes \
  PYTHONPATH=/opt/polymarket-bot/src \
  /usr/local/lib/hermes-agent/venv/bin/python \
  -m polybot.hermes.profile_verify \
  --profile-home /var/lib/polybot-hermes/.hermes/profiles/polymarket
systemctl is-active polymarket-hermes.service   # inactive
systemctl is-enabled polymarket-hermes.service # disabled
```

There must be exactly one such job. Do not run it manually to manufacture a production proposal.
Automatic cron execution begins only when the profile gateway is separately activated.

## 6. Separately approved activation

POL-17 activation and POL-18 activation are distinct approvals. Start without enabling first:

```sh
systemctl start polymarket-ingestion.service
systemctl is-active polymarket-ingestion.service
stat -c '%A %U %G %n' /run/polybot-proposal /run/polybot-proposal/proposal.sock
cat /run/polybot/shadow-status.json

systemctl start polymarket-hermes.service
systemctl is-active polymarket-hermes.service
journalctl -u polymarket-hermes.service --since=-5m --no-pager
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
- the first cron run either emits no proposal or at most one genuine evidence-backed `PROPOSED`
  row; no fake proposal is injected for testing;
- any proposal is independently processed by ERS with fresh-book re-fetch, PaperSigner only, and
  the existing atomic/restart-safe shadow path.

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

Preserve the Hermes profile, cron state, every paper database/WAL/SHM, compact midpoint/trade/news
history, terminal receipts, outboxes, and historical raw-firehose evidence. Never delete data or
recreate `/root/git/polymarket-bot.git` to make rollback look clean. Roll code back only through the
GitHub-linked service checkout. If the brain fails, POL-17 can remain safely paper-idle; never give
the brain SQLite access, a persisted-midpoint fallback, another collector, broader tools, or
membership in `polybot` to work around the failure.
