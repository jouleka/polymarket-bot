Run one bounded paper-market research cycle.

1. Read `get_flags`. Stop without proposing unless the runtime and registry are ready.
   If `live_book_tokens` is empty, stop without proposing.
2. Read a bounded market page. Inspect a book only when that outcome's
   token_id is present in `live_book_tokens`, then read relevant resolved history.
3. At most one proposal may be emitted in this run. Propose only when the market identity, live
   book, probability thesis, price limits, and citations are internally consistent.
4. Every decimal argument must be a JSON string. Use `BUY` only. Suggestions remain untrusted and
   the deterministic ERS may reject, reprice, or downsize them.
5. If evidence is absent, ambiguous, stale, or contradictory, emit no proposal. Never synthesize a
   proposal merely to exercise plumbing.

Do not ask for or attempt to use any tool outside the five presented to you.
