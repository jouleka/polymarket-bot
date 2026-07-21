# VERIFICATION — POL-13 primary-source coverage

**Date:** 2026-07-20
**Reviewed implementation head:** `cc1641be497eb9fbeeb340bd76e38e278191f4f1`
**Result:** live paper/shadow activation PASS

## 1. Outcome

The existing POL-17/POL-18 paper-shadow runtime can now ingest and query relevant official political
and geopolitical evidence without promoting Google News, scanning the persisted EventStore, adding
a collector, or expanding Hermes authority.

The approved PRIMARY additions are exact:

| Source | URL | Independent group |
|---|---|---|
| `whitehouse-news` | `https://www.whitehouse.gov/news/feed/` | `whitehouse.gov` |
| `un-middle-east` | `https://news.un.org/feed/subscribe/en/news/region/middle-east/feed/rss.xml` | `un.org` |
| `war-releases` | `https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=20` | `war.gov` |
| `iaea-news` | `https://www.iaea.org/feeds/topnews` | `iaea.org` |

All content remains `UNTRUSTED`; PRIMARY only makes an exact ID citation eligible. The existing ERS
truth gate still requires independent publisher groups. `google-news-top` remains DISCOVERY.

## 2. Bounded runtime architecture

The existing same-process `NewsPoller` publishes a source snapshot only after fetch, complete parse,
and persistence all succeed. `RecentNewsCache` retains at most 50 items per source and 4,096 content
characters per item. Across the ten configured feeds, retained searchable content is therefore at
most about 2 MiB plus small object overhead.

`get_news(query=...)` searches only that current-feed cache with a printable-ASCII, at-most-128
character, case-insensitive literal substring. `%` and `_` are data, not wildcards. Filtering occurs
before the existing offset/limit projection. A missing cache fails closed. `query=None` continues to
use the existing bounded PRIMARY-first EventStore read.

No production database table, index, writer, path, migration, raw-firehose policy, websocket
collector, or service unit changes. The query does not initiate a web request. The MCP inventory is
still exactly six tools and `propose_trade` remains the only write.

## 3. TDD and whole-slice evidence

Strict serial RED/GREEN checkpoints covered the source identities, cache and poller, read view, RPC,
MCP schema, profile prompt, and whole slice. The first implementation used parameterized SQLite
literal filtering; independent review correctly blocked it because it lowercased every historical
allowlisted content row before pagination. That implementation was removed completely. Persisted
storage has no `content_query` or `instr(lower(content))` diff from `main`.

The corrected whole-slice test proves:

```text
bounded current-feed cache
  -> get_news(query="iran")
  -> exact eligible UN + IAEA citation IDs
  -> propose-only insertion
  -> ERS truth gate + live-book validation
  -> atomic Maker/Shadow execution outbox
  -> stale-book rejection and apply-before-ack restart replay
  -> provider consensus + target failure/retry
  -> terminal resolution fanout
  -> final marks, closed position, and forecast evidence
```

The two proposal citations are taken directly from the returned queried page; the test cannot pass if
the query/evidence block is removed. Sixty newer unrelated PRIMARY items and sixty DISCOVERY items
make the relevance proof non-vacuous.

## 4. Independent specification/security review

The initial review blocked two issues:

1. historical SQLite content matching had unbounded row work and per-row content allocation on the
   synchronous RPC event loop; and
2. SQLite `lower()` is ASCII-only while the draft promised Unicode case-insensitivity.

The bounded poller-owned cache and exact printable-ASCII contract closed both without a database
migration. Closing re-review passed at `7611e8b` with 129 focused tests. After mutation-driven test
strengthening, final review at `cc1641b` confirmed:

- no persisted content-query path;
- hard item/content cache ceilings and atomic source replacement;
- EventStore/cache routing separation and fail-closed missing-cache behavior;
- aligned cache/read/RPC/MCP query validation;
- exact source tiers/groups and Google DISCOVERY status;
- exact-six/no-authority expansion and byte-untouched sacred surfaces; and
- non-vacuous whole-slice crash/restart/terminal coverage.

## 5. Adversarial mutation battery

The first isolated battery executed 45 mutations: 34 were immediately killed and 11 exposed missing
test assertions. The survivors covered retained-content cap deletion, incremental pre-complete cache
publication, wildcard interpretation, five invalid cache inputs, `query=None` cache misrouting,
anti-fabrication prompt removal, and queried-evidence bypass.

All test gaps were closed. The isolated closing rerun killed all 11/11 survivor classes, including a
genuine LIKE/fnmatch `%`/`_` mutation using an `Xmissing` decoy that cannot match the literal
`%_missing`. The restored focused baseline passed 135 tests; exact-six, sacred-surface, diff-clean,
and mutation-residue checks passed. The tmpfs mutation worktree was removed.

## 6. Canonical result and operational boundary

Canonical tmpfs suite at the exact reviewed head:

```text
2410 passed in 10.53s
```

At this evidence point the branch had not been pushed or merged, the service checkout and existing
Hermes profile/cron had not been changed, and both running services remained on the prior reviewed
release. Landing and activation evidence follows.

This capability improves official evidence coverage; it does not manufacture a trading edge or
guarantee a proposal. Unsupported or ambiguous markets must still produce an honest no-trade result.
Official sports data remains a separate reviewed integration.

## 7. Landing and stopped-first installation

Ready PR #51 contained exact reviewed head `541e5df` and merged to `main` as
`e3f04eb465d0515467922454a19910cf988ec598` with no CI or merge conflict. The existing Hermes cron
was idle after completed run 997. Hermes then POL-17 stopped cleanly with exit status zero and no
surviving runtime/MCP process.

Stopped preservation checks passed:

- all seven production SQLite databases returned `quick_check: ok`;
- historical raw-firehose `SHA256SUMS` passed for every recorded artifact;
- production config checksum remained
  `f42f99379627f441e1363a7976430ef8a81c979cb5382c6a62afa587ab499361`;
- all seven database inodes were unchanged; and
- the pre-install database contained zero rows for the four new sources and zero raw `clob-ws` rows.

The service checkout fast-forwarded from `65572e0` to `e3f04eb`. The idempotent installer preserved
the config and databases and left both units stopped/disabled. The existing profile was not recreated:
`config.yaml` and `SOUL.md` checksums stayed unchanged, profile-local auth/environment files remained
absent, MCP stayed pinned to 1.28.1 in both environments, and stopped preflight reported
`exact six; PASS`.

Only existing cron `ad1c2d9b8c30`'s prompt was updated through the native `update_job` API. A
field-by-field comparison proved every non-prompt field unchanged, including five-minute schedule,
enabled state, toolset, model/provider inheritance, and completed count 997. The deployed prompt
SHA-256 is `bebedbddfcad686cb9df347b3b6358ad05edb4fa3b8b8c79d3155d6f19ec2580`.

## 8. Live source, query, and natural-cycle evidence

POL-17 started first and reached `RUNNING` on the second two-second readiness poll. It advertised 186
fresh live-book tokens. All four new feeds returned HTTP 200 during the initial poll and persisted:

```text
iaea-news        15
un-middle-east   30
war-releases     20
whitehouse-news  30
```

A direct production RPC read exercised the process-local bounded cache rather than SQLite. Query
`Iran` returned 18 rows: 13 exact `un-middle-east` PRIMARY/citation-eligible records and five
`google-news-top` DISCOVERY/ineligible records. `Israel` returned eight eligible UN records;
`ceasefire` returned one eligible UN record plus one ineligible Google record. Google was not
promoted, and persisted raw `clob-ws` remained exactly zero.

The existing `gpt-5.6-terra` / `openai-codex` Hermes service then passed its exact-six `ExecStartPre`.
Natural catch-up session `cron_ad1c2d9b8c30_20260721_080014` completed `ok` as run 998. It used flags,
nearest-deadline markets, a fresh book, resolved history, and
`get_news(query="Dplus", limit=20)`, then returned `[SILENT]`. Natural session
`cron_ad1c2d9b8c30_20260721_080612` completed `ok` as run 999 with the same ordered reads and
`get_news(query="Rolster", limit=20)`, then returned `[SILENT]`. Both selected markets were sports;
the bounded official political/geopolitical set contained no relevant eligible evidence, so silence
was the required honest result. No production proposal was synthesized.

Both services are active and enabled with `NRestarts=0`. The final observation showed current/peak
memory of 103,960,576/110,968,832 bytes for POL-17 and 265,838,592/268,251,136 bytes for Hermes,
zero swap and zero `high`, `max`, OOM, or OOM-kill events. Profile-local auth/environment files remain
absent. Production still has zero pending intents, fills, execution outbox/receipts, forecasts,
maker fills, shadow trades, resolution outbox/terminals, and raw `clob-ws` rows. Config checksum and
all database inodes remain unchanged.
