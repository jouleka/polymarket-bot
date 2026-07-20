# POL-17 websocket-silence resnapshot plan

1. Add one production-shaped test: real book, responsive `PONG` at the silence threshold,
   reconnect, replacement snapshot. Observe RED because only one connection is made.
2. Add the minimum `MarketSocket` silence-resnapshot path and observe focused GREEN.
3. Add boundary and authority pins proving a pre-threshold `PONG` neither reconnects nor advances
   market health, plus constructor validation.
4. Run socket, sharding, anomaly, runtime, and whole-slice focused suites.
5. Run the complete canonical suite.
6. Mutate `>=` to `>`, stamp health on `PONG`, omit pre-await staleness, omit reconnect, and count
   silence as divergence; require a named test failure for each mutation.
7. Re-run the complete suite, review the diff against the design, and record verification evidence.
8. Push and merge, install while preserving production databases/configuration, controlled-restart
   the already-approved paper services, and verify clean reconciliation, current books, memory,
   outboxes, and persistence shape.
