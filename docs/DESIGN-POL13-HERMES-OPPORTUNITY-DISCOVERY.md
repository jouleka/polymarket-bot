# DESIGN — POL-13: Hermes opportunity discovery correction

**Date:** 2026-07-20 · **Status:** owner-approved corrective contract

## 1. Problem and scope

The live shadow showed that POL-18 is operational but cannot perform its intended proposal job:

- `get_market(offset=0)` is ordered by `condition_id`, so every cron turn repeatedly researches the
  same arbitrary page instead of the nearest still-open markets;
- a page does not say which outcome books are currently usable, while `get_flags` advertises a long
  token list that can be truncated before the model sees all of it; and
- the master design requires Hermes to read the normalized sanitized news store, but POL-18 exports
  no such read. Hermes therefore cannot know valid EventStore citation IDs, and ERS correctly gives
  uncited proposals no news weight.

This correction restores that missing read path and fixes deterministic opportunity discovery. It
does not weaken citation truth-gating or add any execution authority. Adding or promoting external
PRIMARY sources is explicitly out of scope and remains a separate operator trust/legal decision.

## 2. Architecture

The existing same-process proposal server remains the only boundary. The facade gains one optional,
read-only `news_reader`; the MCP/RPC inventory becomes exactly six methods:

```
get_flags      readiness and conservative safety facts
get_market     nearest positive-resolution markets first + live-book facts
get_book       one shared fresh LocalBook top of book
get_news       bounded sanitized allowlisted EventStore evidence
get_ledger     bounded resolved forecast history
propose_trade  the unchanged INSERT-only untrusted proposal write
```

`NewsReadView` reads through POL-17's existing `ReadOnlyEventStore`. A bounded SQL query selects only
configured allowlist source names and orders newest observations first. The projection exposes only
source name/tier/group, stable citation ID, published timestamp, and already-sanitized content.
Venue rows, raw websocket data, arbitrary SQL, store handles, credentials, and mutation methods are
never exposed.

`MarketReadView` keeps exact selectors but orders page results by the tuple
`(seconds_to_resolution == 0, seconds_to_resolution, condition_id)`. Thus expired-but-not-yet-closed
rows cannot displace the nearest positive deadline. When a live-token provider is configured, each
outcome contains a boolean `live_book`; the callable is sampled once per request.

## 3. Exact contracts

```python
class NewsReadView:
    def __init__(self, event_store, *, allowlist, default_limit=25,
                 max_limit=50, max_content_chars=4096): ...
    def __call__(self, *, offset=0, limit=None): ...

class MarketReadView:
    def __init__(self, registry_provider, *, default_limit=25, max_limit=50,
                 live_token_ids=None): ...

class ProposeOnlyFacade:
    def __init__(self, store, *, market_reader=None, book_reader=None,
                 ledger_reader=None, flags_reader=None, news_reader=None): ...
    def get_news(self, *args, **kwargs): ...
```

`None` for either new optional seam preserves existing unit/advisory construction. The production
root supplies both seams. `get_news` accepts only bounded integer `offset` and `limit` parameters.

## 4. Safety invariants

1. `propose_trade` remains byte-for-byte INSERT-only and is still the sole Hermes write.
2. No signer, order, cancellation, wallet, key, redemption, chain-write, database-write, or runtime
   control capability is added.
3. News remains untrusted spotlight-delimited data. The view never fetches a citation and never
   changes trust tier; only the deterministic ERS truth gate decides whether citations count.
4. The projection accepts only sources whose persisted tier matches the pinned allowlist tier.
5. Every page and content field is bounded. The SQL path does not materialize the production market
   firehose and does not add persistence or an index migration.
6. Market urgency is deterministic. The model cannot choose or change ordering logic.
7. Live-book availability is sampled from POL-17's shared in-memory books; persisted midpoint rows
   never become execution authority.
8. All existing re-fetch, exact Decimal, controller, apply-before-ack, terminal precedence, and
   paper-only seams remain unchanged.

## 5. Runtime prompt and deployment

The one existing cron job remains non-overlapping and every five minutes to preserve the reviewed
memory/API envelope. Its prompt must inspect the nearest positive-resolution live-book market, read
relevant sanitized news, and cite only IDs actually returned by `get_news`. No proposal is required;
absent or contradictory evidence remains an honest no-trade result.

Installation, service restart, and live observation occur only after the full suite, independent
review, and mutation gate. No live-money activation exists in this slice.

## 6. Acceptance criteria

1. A page with mixed deadlines returns positive deadlines ascending, zero-second rows last, with a
   stable condition-ID tie break.
2. Production-wired market rows truthfully identify live and unavailable books without a second
   collector or persisted-book authority.
3. `get_news` returns only bounded allowlisted, tier-consistent, sanitized evidence newest first and
   supplies the exact citation ID ERS can verify.
4. RPC, MCP, authored config, effective inventory, cron inventory, and verifier agree on exactly six
   tools; missing or extra tools fail closed.
5. The facade still exposes no mutation/signing path beyond unchanged `propose_trade`.
6. The cron prompt selects nearest positive deadlines and never invents a citation or proposal.
7. Focused tests, the canonical full suite, independent specification/security review, and isolated
   mutations all pass before any installation.

