# DESIGN — POL-13 primary-source coverage

**Date:** 2026-07-20
**Status:** owner-approved implementation contract

## 1. Problem

POL-17 and the exact-six Hermes profile are running correctly, but the existing PRIMARY news set is
limited to US financial agencies. Urgent live markets are often geopolitical or political. The
bounded `get_news` page therefore contains truthful but irrelevant evidence and Hermes correctly
stays silent.

The correction must improve relevant official-source discovery without promoting Google News,
duplicating collectors, synthesizing proposals, increasing Hermes authority, or weakening ERS.

## 2. Approved source identities

The owner explicitly approved these four official publishers as PRIMARY:

| Name | Exact feed URL | Publisher group |
|---|---|---|
| `whitehouse-news` | `https://www.whitehouse.gov/news/feed/` | `whitehouse.gov` |
| `un-middle-east` | `https://news.un.org/feed/subscribe/en/news/region/middle-east/feed/rss.xml` | `un.org` |
| `war-releases` | `https://www.war.gov/DesktopModules/ArticleCS/RSS.ashx?ContentType=9&Site=945&max=20` | `war.gov` |
| `iaea-news` | `https://www.iaea.org/feeds/topnews` | `iaea.org` |

All feed content remains `UNTRUSTED`. PRIMARY is only citation eligibility; the existing truth gate
still requires independent publisher groups before non-tiny sizing. `google-news-top` remains
DISCOVERY and cannot become citation eligible.

State Department and MLB candidates are excluded because their live responses do not pass the
existing safe feed parser. Official sports coverage requires a separate reviewed data integration.

## 3. Exact contract

### 3.1 Allowlist

`DEFAULT_ALLOWLIST` adds exactly the four identities above. Names, URLs, tiers, and publisher groups
are exact and unique. The existing six entries remain unchanged.

### 3.2 Bounded evidence query

`NewsReadView` gains a backward-compatible optional query seam and an optional dedicated query
provider:

```python
RecentNewsCache(max_items_per_source=50, max_content_chars=4096)
NewsReadView(event_store, ..., query_store=None)
NewsReadView.get_news(*, offset=0, limit=None, query=None)
```

The existing same-process `NewsPoller` atomically replaces one source's cache snapshot only after a
successful fetch and parse. The cache retains at most 50 current items per configured source and at
most 4,096 content characters per item. It is rebuilt by normal polling after restart and is neither
persisted nor a second collector. Production passes it as `query_store`; a query fails closed when
that bounded provider is absent. Unqueried reads continue to use the existing EventStore path.

The existing facade, RPC, and MCP `get_news` method accepts optional `query`:

```text
get_news(offset=0, limit=None, query=None)
```

When present, `query` must be an exact non-empty printable ASCII string of at most 128 characters.
Matching is a case-insensitive literal substring over the bounded current-feed cache before ordering,
limit, and offset. `%` and `_` have no wildcard meaning. No persisted-history scan, web fetch, SQL
syntax, regex, semantic search, source selection, or tier promotion is exposed.

`None` preserves the current PRIMARY-first, newest-within-tier behavior byte-for-byte. Existing
bounds remain: offset at most 1,000, limit at most 50, projected content at most 4,096 characters,
and citation identifiers at most 2,048 characters.

### 3.3 Hermes behavior

The existing profile remains the same installation, model, auth, schedule, and exact-six tool
grant. Its cron prompt selects a market first, then requests one bounded literal market-relevant
term with `get_news(query=..., limit=20)`. Empty or irrelevant results mean no proposal. Hermes must
not invent citations or submit a proposal merely to create activity.

## 4. Safety and resource invariants

- Paper/shadow only; `PaperSigner` remains the only signer.
- No key, wallet, signing, order, cancellation, redemption, chain-write, sizing, pricing, runtime
  control, or deployment authority is added to Hermes.
- `propose_trade`, `evaluate_intent`, controller caps, signer protocol, and live-book re-fetch seams
  remain byte-for-byte untouched.
- Query input cannot select sources or tiers and cannot make DISCOVERY content citation eligible.
- Query work is bounded to at most 50 items and 4,096 content characters per configured source; it
  never scans or materializes the EventStore and does not create another collector.
- Feed polling continues to isolate an individual source failure; it cannot terminate POL-17.
- Production persists the existing downsampled midpoint batches and deduplicated trade tape, with
  zero raw CLOB websocket rows.
- Service memory limits and exact-six preflight remain unchanged.

## 5. Runtime and deployment

The sources and bounded cache use the existing POL-17 same-process `NewsPoller`; no unit, database,
writer, network request path, or live-book transport is added. Installation is a stopped
fast-forward of the existing service checkout and an in-place update of the existing profile prompt.
Production databases are not migrated or replaced.
Services are restarted in the existing order: Hermes stop, POL-17 stop, install/preflight, POL-17
start/readiness, Hermes start. Existing enablement is preserved.

## 6. Acceptance criteria

| ID | Criterion |
|---|---|
| A1 | The default allowlist contains the existing six entries plus exactly the four approved PRIMARY identities. |
| A2 | Google remains DISCOVERY and publisher groups remain exact and independent. |
| A3 | `query=None` preserves existing bounded PRIMARY-first behavior. |
| A4 | A valid query performs case-insensitive literal filtering over the bounded current-feed cache before pagination; `%` and `_` are literal. |
| A5 | Empty, over-128, non-ASCII, control-bearing, boolean, and non-string queries fail closed across cache/read-view/RPC. |
| A6 | MCP exposes only the same six tools and describes the bounded literal query. |
| A7 | The profile instructs one market-relevant bounded query and forbids fabricated citations/proposals. |
| A8 | A whole-slice test proves relevant official evidence remains discoverable behind unrelated PRIMARY traffic and continues through proposal, ERS, atomic shadow outbox, restart replay, resolution fanout, and terminal evidence. |
| A9 | Independent spec/security review and an isolated adversarial mutation battery have zero unresolved findings/survivors. |
| A10 | The canonical suite passes; production verification preserves exact-six authority, zero raw CLOB rows, database integrity, and memory limits. |

## 7. Out of scope

Sports-provider integration, semantic or multi-term search, arbitrary feed registration, source-tier
administration, proposal generation changes, sizing/price/signing changes, real-money activation,
and any guarantee that a proposal or trade will occur are out of scope.
