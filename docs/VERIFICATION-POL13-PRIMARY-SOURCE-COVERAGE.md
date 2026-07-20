# VERIFICATION — POL-13 primary-source coverage

**Date:** 2026-07-20
**Reviewed implementation head:** `cc1641be497eb9fbeeb340bd76e38e278191f4f1`
**Build result:** PASS; production installation pending

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

At this evidence point the branch has not been pushed or merged, the service checkout and existing
Hermes profile/cron have not been changed, and both running services remain on the prior reviewed
release. Landing, stopped-first installation, in-place cron prompt reconciliation, source polling,
bounded live query proof, and natural-cycle observation must be recorded separately.

This capability improves official evidence coverage; it does not manufacture a trading edge or
guarantee a proposal. Unsupported or ambiguous markets must still produce an honest no-trade result.
Official sports data remains a separate reviewed integration.
