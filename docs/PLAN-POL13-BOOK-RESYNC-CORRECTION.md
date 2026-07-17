# PLAN — POL-13 repeated order-book resync correction

Status at 2026-07-17 13:45 UTC: Stage A and Stage B implementation are complete. Independent
reviews found normal-close readiness, semantic-validation, bounded-history/log-safety, lock, and
coverage gaps; all have focused GREEN fixes. Mutation completion, re-review, final canonical suite,
documentation/publication, installation, and observation remain.

## Stage A — identify the real divergence

1. Add one failing `MarketStream` test requiring bounded exact divergence evidence and proving a
   later clean frame cannot erase it. Observe RED.
2. Add the minimum immutable value and paired consume seam. Run focused GREEN and checkpoint.
3. Add one failing `MarketSocket` test requiring the terminal error to identify the offending
   asset, reconstructed/venue top, and shard asset count. Observe RED.
4. Add minimum error propagation without changing retry/backoff limits. Run focused GREEN and
   checkpoint.
5. Add a bounded no-persistence live diagnostic entry point with a focused construction test.
   Observe RED, implement, and run focused GREEN.
6. Run the collector-focused suite and canonical suite. Stop services in dependency order and run
   the bounded single-collector diagnostic.

## Stage B — correct only the observed class

7. Amend the design with the captured evidence and exact recovery rule.
8. Add one real failing regression reproducing the captured frame/snapshot sequence. Observe RED.
9. Implement the minimum fail-closed correction. Run focused GREEN and checkpoint.
10. Add the whole-slice restart/sibling/global-format test, observe RED if it introduces a new
    required seam, then implement minimum GREEN.
11. Run focused tests and the canonical suite.

## Closeout

12. Perform independent specification/security review and fix every finding serially.
13. Run an isolated adversarial mutation battery; close every survivor and re-review.
14. Rerun the canonical suite, update verification/HANDOFF/TICKETS and YouTrack, then publish and
    merge only the reviewed candidate.
15. Install with databases/config preserved, validate memory caps and no-signing boundaries, and
    restart POL-17 then Hermes. Observe the corrected failure window and record evidence.
