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

Existing optional seams gain a backward-compatible `None` default:

```python
EventStore.recent_by_sources(..., priority_sources=(), content_query=None)
ReadOnlyEventStore.recent_by_sources(..., priority_sources=(), content_query=None)
NewsReadView.get_news(*, offset=0, limit=None, query=None)
```

The existing facade, RPC, and MCP `get_news` method accepts optional `query`:

```text
get_news(offset=0, limit=None, query=None)
```

When present, `query` must be an exact non-empty string of at most 128 Unicode code points with no
control/format/surrogate/private-use/unassigned characters. Matching is a case-insensitive literal
substring over stored `content`, performed inside parameterized SQLite before projection, ordering,
limit, and offset. `%` and `_` have no wildcard meaning. No web fetch, SQL syntax, regex, semantic
search, source selection, or tier promotion is exposed.

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
- Query work is bounded in SQL and does not materialize the EventStore or create another collector.
- Feed polling continues to isolate an individual source failure; it cannot terminate POL-17.
- Production persists the existing downsampled midpoint batches and deduplicated trade tape, with
  zero raw CLOB websocket rows.
- Service memory limits and exact-six preflight remain unchanged.

## 5. Runtime and deployment

The sources use the existing POL-17 same-process `NewsPoller`; no unit, database, writer, or live-book
transport is added. Installation is a stopped fast-forward of the existing service checkout and an
in-place update of the existing profile prompt. Production databases are not migrated or replaced.
Services are restarted in the existing order: Hermes stop, POL-17 stop, install/preflight, POL-17
start/readiness, Hermes start. Existing enablement is preserved.

## 6. Acceptance criteria

| ID | Criterion |
|---|---|
| A1 | The default allowlist contains the existing six entries plus exactly the four approved PRIMARY identities. |
| A2 | Google remains DISCOVERY and publisher groups remain exact and independent. |
| A3 | `query=None` preserves existing bounded PRIMARY-first behavior. |
| A4 | A valid query performs case-insensitive literal SQL filtering before pagination; `%` and `_` are literal. |
| A5 | Empty, over-128, control-bearing, boolean, and non-string queries fail closed across storage/read-view/RPC. |
| A6 | MCP exposes only the same six tools and describes the bounded literal query. |
| A7 | The profile instructs one market-relevant bounded query and forbids fabricated citations/proposals. |
| A8 | A whole-slice test proves relevant official evidence remains discoverable behind unrelated PRIMARY traffic and continues through proposal, ERS, atomic shadow outbox, restart replay, resolution fanout, and terminal evidence. |
| A9 | Independent spec/security review and an isolated adversarial mutation battery have zero unresolved findings/survivors. |
| A10 | The canonical suite passes; production verification preserves exact-six authority, zero raw CLOB rows, database integrity, and memory limits. |

## 7. Out of scope

Sports-provider integration, semantic or multi-term search, arbitrary feed registration, source-tier
administration, proposal generation changes, sizing/price/signing changes, real-money activation,
and any guarantee that a proposal or trade will occur are out of scope.
