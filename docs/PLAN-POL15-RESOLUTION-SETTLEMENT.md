# POL-15 — D2 resolution and settlement feed implementation plan

**Design:** [DESIGN-POL15-RESOLUTION-SETTLEMENT.md](DESIGN-POL15-RESOLUTION-SETTLEMENT.md)
**Execution:** strict serial TDD. Every cycle below starts with one named test (parameterization is
allowed only for the single stated invariant), observes the intended RED, adds minimum production
code, observes GREEN, and commits before the next cycle.

## Baseline and scope

- Base: `main` / feature HEAD `77dddef`.
- Baseline: 1,482 tests passing.
- New package: `src/polybot/resolution/`.
- Existing integrations: MarketRegistry, ERS service, ForecastLedger, MakerLedger, ShadowLedger, and
  their evidence consumers.
- No service start, deployment, risk-cap change, signing, or blockchain transaction.

## Task 0 — Freeze the lean contract

1. Replace the blocked oversized draft with the owner-approved lean design and this plan.
2. Remove stale planning artifacts whose validator did not validate its manifest.
3. Run Markdown-link, whitespace, baseline, and independent architecture/ABI/verification reviews.
4. Correct every finding, re-review exact bytes, and commit Task 0 before production code.

## Task 1 — Pure authority models

### M01 — Subject identity

RED `test_resolution_subject_requires_exact_binary_identity`: pin frozen valid construction and one
table of malformed event/category/condition/token forms, including duplicate token strings. Candidate
order is syntactically valid here and is authenticated on chain in R05.

GREEN: add `ResolutionSubject` validation in `resolution/models.py`.

### M02 — Exact payout arithmetic

RED `test_payout_vector_preserves_every_valid_binary_fraction`: accept scaled/unequal integer vectors,
return exact `Fraction` authority, and reject invalid denominator/numerator/slot inputs.

GREEN: add `PayoutVector` and `fraction_for`.

### M02b — Deterministic Decimal projection

RED `test_decimal_projection_ignores_ambient_context`: `1/3` produces the pinned 78-digit
ROUND_HALF_EVEN value under hostile low/high global contexts without mutating either.

GREEN: add fixed local-context `decimal_for`.

### M03 — Observation lifecycle contract

RED `test_provider_observation_separates_phase_from_payout_and_path`: unresolved forbids terminal
fields; finalized observations enforce the exact typed authority fields.

GREEN: add lifecycle/path enums and `ProviderObservation` validation.

### M04 — Path precedence

RED `test_path_precedence_is_manual_disputed_unknown_clear`: pin the pure fold and empty-input failure.

GREEN: add `fold_dispute`.

### M05 — Dual-provider terminal

RED `test_terminal_requires_two_distinct_matching_finalized_observations`: only equal non-UNKNOWN
observations create a terminal; provider IDs sort; any other field mismatch is unavailable.

GREEN: add `TerminalResolution.from_observations`.

### M06 — Exact terminal bytes

RED `test_terminal_v1_canonical_bytes_and_hash`: assert the complete 997-byte non-ASCII design vector,
SHA-256, field presence, provider reordering invariance, replay equality, and no wall-clock field.

GREEN: add the sole serializer in `canonical.py` and exact terminal payload projection.

## Task 2 — Canonical market/row identity

### I01 — Registry resolution subject

RED `test_registry_returns_resolution_subject_after_three_identifier_check`: expose event, condition,
category, selected token slot, and ordered siblings; mismatches remain unavailable.

GREEN: add frozen `ResolutionSubjectMetadata` and `MarketRegistry.resolution_subject_for` without an
ERS-to-resolution-package dependency.

### I02 — Forecast v0 migration

RED `test_forecast_v0_database_migrates_to_nullable_identity`: opening a pre-POL-15 database adds the
exact identity/rational columns and returns the old row with appended default fields.

GREEN: add additive Forecast schema migration and appended record fields.

### I03 — Forecast canonical creation

RED `test_forecast_canonical_identity_is_all_or_none_and_slot_matches_token`: exact identity inserts;
mixed identity and slot/token mismatch fail before insertion.

GREEN: extend `record_forecast` with explicit optional identity arguments.

### I04 — Maker v0 migration

RED `test_maker_v0_database_migrates_to_nullable_identity`: independently prove exact added columns
including rational authority, and old-record defaults.

GREEN: add the Maker migration and appended fields.

### I05 — Maker canonical creation

RED `test_maker_canonical_identity_is_all_or_none_and_slot_matches_token`.

GREEN: extend `record_fill` with explicit optional identity arguments.

### I06 — Shadow v0 migration

RED `test_shadow_v0_database_migrates_to_nullable_identity`: independently prove exact added columns
including rational authority, and old-record defaults.

GREEN: add the Shadow migration and appended fields.

### I07 — Shadow canonical creation

RED `test_shadow_canonical_identity_is_all_or_none_and_slot_matches_token`.

GREEN: extend `record_trade` with explicit optional identity arguments.

### I08 — ERS real-registry identity write

RED `test_ers_real_registry_records_canonical_resolution_identity`: the reconciled subject is written
with the forecast before any future settlement path.

GREEN: resolve and pass the registry subject in `ers/service.py`.

### I09 — ERS legacy boundary

RED `test_only_explicit_stub_market_meta_may_write_legacy_forecast`: `StubMarketMeta` keeps old tests;
an arbitrary metadata object missing/unable to supply subject fails before component/forecast writes.

GREEN: make the legacy branch an explicit type check and add a distinct fail-closed reason.

### I10 — Terminal race audit behavior

RED `test_ers_terminal_race_never_writes_forecast_or_reaches_signing`: a receipt precheck rejects
before component write; a receipt racing after the component may leave only that audit component but
never a forecast, ACCEPT decision, or signature; the fail-closed REJECT reason is recorded.

GREEN: add ForecastLedger condition-open check and the narrow ERS catch/reason.

## Task 3 — Immutable target application

### T01 — Forecast clear projection

RED `test_forecast_clear_terminal_projects_exact_slot_value`: binary maps WON/LOST and fractional maps
VOID while retaining exact numerator/denominator, deterministic Decimal projection, and terminal ID.

GREEN: create Forecast receipts and clear projection in one transaction.

### T02 — Forecast excluded projection

RED `test_forecast_disputed_or_manual_terminal_is_non_economic`: map both paths to DISPUTED_LOST with
null value and immutable terminal ID.

GREEN: add the excluded branch.

### T03 — Forecast all-row atomicity

RED `test_forecast_terminal_conflict_rolls_back_every_row_and_receipt`: a later matching-row identity
conflict leaves earlier rows and receipt untouched.

GREEN: validate every row before receipt/update in the same transaction.

### T04 — Forecast zero-row receipt fence

RED `test_forecast_zero_row_receipt_blocks_later_creation`: persist the receipt on zero matches;
reapply returns zero; later row creation for that condition fails.

GREEN: add receipt-first condition fence to creation.

### T05 — Maker terminal application

RED `test_maker_terminal_projects_clear_and_excluded_values`: exact CLEAR fraction uses SETTLED;
its rational columns are exact; DISPUTED/MANUAL use DISPUTED/null.

GREEN: implement Maker target application.

### T06a — Maker all-row atomicity

RED `test_maker_terminal_conflict_rolls_back_every_row_and_receipt`: a later row conflict leaves no
partial settlement.

GREEN: validate every row before receipt/update in one transaction.

### T06b — Maker zero-row receipt fence

RED `test_maker_zero_row_receipt_blocks_later_creation`: zero matches still persists a receipt and
later creation fails.

GREEN: add the receipt-first creation fence.

### T06c — Maker receipt replay

RED `test_maker_receipt_replay_is_idempotent_and_payload_conflict_fails`: exact replay returns zero;
same terminal ID with changed bytes raises without mutation.

GREEN: compare immutable receipt bytes.

### T07 — Shadow terminal application

RED `test_shadow_terminal_projects_clear_and_excluded_values`: independently pin Shadow projection.

GREEN: implement Shadow target application.

### T08a — Shadow all-row atomicity

RED `test_shadow_terminal_conflict_rolls_back_every_row_and_receipt`.

GREEN: validate every row before receipt/update in one transaction.

### T08b — Shadow zero-row receipt fence

RED `test_shadow_zero_row_receipt_blocks_later_creation`.

GREEN: add the receipt-first creation fence.

### T08c — Shadow receipt replay

RED `test_shadow_receipt_replay_is_idempotent_and_payload_conflict_fails`.

GREEN: compare immutable receipt bytes.

### T09 — Legacy mutator authority fence

RED `test_legacy_settlement_mutators_reject_canonical_pending_rows`: all three old mutators work only
for all-null legacy identity and reject canonical rows before or after terminal application.

GREEN: add the canonical identity fence without removing fixture APIs.

### T10 — Durable target commits

RED `test_target_ledgers_use_full_synchronous_durability`: every target connection reports WAL/FULL
after fresh creation and migration.

GREEN: upgrade all three ledger durability settings.

### T11 — Fractional calibration exclusion

RED `test_calibration_excludes_fractional_void_with_exact_value`: retained value never enters binary
Brier pairs.

GREEN: teach CalibrationTracker only.

### T12 — Fractional maker economics

RED `test_maker_tracker_uses_settled_fractional_mark`: SETTLED is honest economic data and missing
marks still fail closed.

GREEN: teach MakerTracker only.

### T13 — Fractional window PnL

RED `test_window_net_uses_settled_fractional_mark`.

GREEN: teach `harness/pnl.py` only.

### T14 — Fractional evidence selection

RED `test_evidence_evaluator_keeps_settled_fractional_shadow_rows`: OOS selection includes SETTLED;
disputed/manual rows remain excluded and counted.

GREEN: teach `harness/evidence.py` only.

## Task 4 — Central store

### S01 — Assessment persistence

RED `test_assessment_round_trips_and_replaces_only_same_subject`: unresolved/UNKNOWN state persists;
subject conflict fails across restart.

GREEN: initialize the FULL/WAL/foreign-key store and assessment schema/API.

### S02 — Atomic terminal/outbox creation

RED `test_terminal_atomically_creates_three_ordered_outbox_rows`: one transaction creates terminal and
FORECAST/MAKER/SHADOW rows, deletes a prior assessment, and makes later assessment writes conflict;
injected pre-commit failure leaves the prior state intact.

GREEN: implement `accept_terminal`.

### S03 — Terminal idempotency and conflict

RED `test_store_preserves_first_terminal_bytes`: exact duplicate returns false; changed payload raises
without changing terminal/outbox.

GREEN: add immutable duplicate comparison.

### S04 — Pending order and acknowledgement

RED `test_outbox_order_and_matching_acknowledgement_are_exact`: sequence/role order is stable; only
matching sequence, role, and terminal ID can acknowledge; replay returns false.

GREEN: implement pending/ack APIs.

### S05 — One-way integrity halt

RED `test_integrity_halt_persists_and_blocks_mutators`: first reason survives restart and every
assessment/accept/ack/recovery-completion mutator fails closed.

GREEN: implement `halt` and `require_healthy`.

### S06 — Reopen recovery barrier

RED `test_reopened_pending_outbox_requires_complete_recovery`: fresh same-process acceptance is
deliverable, but reopen sets the barrier and only the exact current pending terminal-ID set clears it.

GREEN: implement `recovery_required`, `pending_terminals`, and `_complete_recovery`.

## Task 5 — Two-provider feed

### F01 — Provider-set authority

RED `test_feed_requires_exactly_two_distinct_polygon_providers`: wrong chain or duplicate/empty IDs
fail without a store write.

GREEN: implement feed construction/provider validation.

### F02 — Five-confirmation coordinate

RED `test_feed_uses_lower_head_minus_exactly_five`: negative coordinate and block-hash disagreement
are unavailable without writes.

GREEN: implement common acceptance coordinate.

### F03 — Unresolved result

RED `test_poll_persists_matching_unresolved_assessment`: no terminal/outbox is created.

GREEN: implement unresolved reconciliation.

### F04 — Unknown result

RED `test_poll_persists_matching_unknown_as_excluded_assessment`: exact payout may be retained, but no
terminal/outbox is created.

GREEN: implement UNKNOWN reconciliation.

### F05 — Clear terminal result

RED `test_poll_accepts_matching_clear_terminal`: create immutable terminal and three outbox rows.

GREEN: implement CLEAR acceptance.

### F06 — Disputed/manual terminal result

RED `test_poll_accepts_classified_excluded_terminal`: DISPUTED/MANUAL create immutable outbox payloads
whose path remains non-economic.

GREEN: implement classified excluded acceptance.

### F07 — Per-condition unavailability

RED `test_poll_isolates_retryable_unavailability_in_input_order`: one provider failure/mismatch returns
UNAVAILABLE while a later independent subject is still processed.

GREEN: add bounded result handling.

### F08 — Repeat poll at later head

RED `test_repeat_poll_verifies_original_terminal_coordinate`: later heads cannot create a new payload;
the stored acceptance hash/economics/identity are verified without rescanning logs and returns
ALREADY_TERMINAL.

GREEN: add the stored-terminal branch before current-head derivation.

### F09 — Terminal verification contradiction

RED `test_verify_terminal_halts_on_any_original_authority_change`: acceptance hash (which commits
path/audit history), payout, deployment code, collateral, or token mapping change persistently halts.

GREEN: implement `verify_terminal` against full immutable bytes.

### F10 — Complete pending recovery

RED `test_recover_pending_verifies_all_before_clearing_barrier`: successful all-terminal verification
clears the exact store barrier and returns the count.

GREEN: implement success choreography.

### F11 — Unavailable pending recovery

RED `test_recover_pending_provider_unavailable_keeps_barrier_and_delivers_nothing`.

GREEN: keep recovery all-or-nothing for retryable failures.

## Task 6 — Strict JSON-RPC provider

### R01 — JSON-RPC envelope

RED `test_rpc_correlates_monotonic_request_id`: exact version/ID/result-error envelope is required;
malformed variants are one envelope-invariant parameter table.

GREEN: implement `JsonRpcClient.call`.

### R02 — Wire scalars

RED `test_rpc_quantity_and_fixed_bytes_decoders_are_canonical`: pin bool/leading-zero/empty/width/hex
rejections as one scalar-decoding table.

GREEN: add pure decoders.

### R03 — CTF ABI words

RED `test_ctf_static_calls_decode_exact_32_byte_words`: pin all five frozen selectors and malformed
word rejection.

GREEN: add call encoding/static uint and bytes32 decoding.

### R03b — Frozen deployment coordinates

RED `test_provider_verifies_code_transition_for_ctf_and_selected_adapter`: both providers must observe
empty code at the preceding frozen block and non-empty code at the exact deployment block.

GREEN: add strict `eth_getCode` authority checks cached for the provider instance.

### R04 — CTF payout state

RED `test_provider_reads_binary_ctf_payout_at_requested_block`: exact slot count/denominator/vector and
unresolved denominator zero behavior; never use latest tag.

GREEN: implement state reads.

### R05 — Chain-derived token slots

RED `test_provider_derives_pusd_positions_in_slot_order`: exact Gamma order passes; swapped order or
different collateral is unavailable.

GREEN: compose collection/position calls and decimal token comparison.

### R06a — Monotonic authority coordinates

RED `test_provider_binary_searches_exact_preparation_and_resolution_transitions`: pin deployment
lower bound, first nonzero slot-count block, first positive-denominator block, and logarithmic call
ceiling without a genesis log scan.

GREEN: implement transition binary search.

### R06b — Condition authority linkage

RED `test_ctf_events_tie_condition_adapter_question_and_payout`: exactly one matching preparation and
resolution event at the derived transition blocks is required; CTF resolution precedes the adapter
terminal event in the same transaction; unsupported adapter becomes UNKNOWN.

GREEN: decode the frozen indexed CTF events.

### R07 — UMA v1 path

RED `test_v1_path_never_claims_normal_resolution_is_clear`: normal/update are UNKNOWN; reset is
DISPUTED; flag/emergency is MANUAL; update remains in audit evidence when higher precedence creates a
terminal. V1 adapter events alone cannot prove no DVM dispute.

GREEN: implement v1 normalizer.

### R08 — UMA v2_plus path

RED `test_v2_plus_path_normalizes_normal_reset_flag_and_emergency`: pin the shared v2/v3 layouts and
manual precedence, including unflag not erasing history.

GREEN: implement v2+ normalizer.

### R09 — Positive path completeness

RED `test_empty_unrelated_or_missing_positive_resolution_is_unknown`: absence of bad events can never
be CLEAR.

GREEN: enforce positive request-to-resolution evidence.

### R10a — Derived log page boundaries

RED `test_adapter_history_pages_only_derived_interval_in_exact_10000_block_ranges`: no genesis or
post-resolution range; first/boundary/final arithmetic has no gap or overlap.

GREEN: implement derived-range page generation and version-specific topic filters.

### R10b — Log order and duplicate integrity

RED `test_log_normalization_orders_exact_duplicates_and_rejects_coordinate_conflict`: exact duplicates
collapse; deterministic chain order is returned; different logs at one coordinate fail.

GREEN: implement normalized merge.

### R10c — Failed filtered history

RED `test_failed_filtered_history_is_unavailable_never_unknown_or_clear`.

GREEN: make history retrieval all-or-nothing.

### R11 — Typed provider observation

RED `test_json_rpc_provider_returns_fully_bound_observation`: compose block, CTF, token, and path proof
at one requested block with no live network call.

GREEN: finish `JsonRpcResolutionProvider.observe`.

### R12 — Bounded terminal verification

RED `test_provider_terminal_verification_uses_stored_block_without_log_rescan`: require exact block
hash, CTF state, code anchors, and token mapping; issue zero `eth_getLogs` calls.

GREEN: implement provider `verify_terminal`.

## Task 7 — Durable dispatch

### D01 — Role application and order

RED `test_dispatcher_applies_oldest_role_then_acknowledges`: exact FORECAST/MAKER/SHADOW order and
return count.

GREEN: implement dispatcher bindings and bounded drain.

### D02 — Crash after target commit

RED `test_dispatch_retry_after_target_commit_is_idempotent`: injected crash leaves central pending;
retry sees receipt, returns zero target changes, and acknowledges once.

GREEN: keep target/central transactions separate and expose a test-only post-apply hook.

### D03 — Transient target failure

RED `test_transient_target_failure_stops_without_ack_or_overtake`.

GREEN: stop on ordinary exception and retain order.

### D04 — Target conflict halt

RED `test_target_settlement_conflict_persistently_halts_central_store`: leave outbox pending, record
halt, re-raise, and block later acceptance/drain.

GREEN: special-case `SettlementConflict`.

### D05 — Recovery barrier

RED `test_dispatcher_refuses_reopened_or_partially_recovered_store`: no target method is called until
complete current-process recovery succeeds.

GREEN: enforce store health/recovery before loading outbox.

## Task 8 — Whole-slice verification

1. Fake-provider whole slice: unresolved, UNKNOWN, clear binary, clear fractional, disputed/manual,
   repeat poll, disagreement isolation, restart recovery, and post-acceptance contradiction.
2. Three-ledger whole slice: one classified terminal fans out, crash-retries safely, exact slots and
   tail counters are correct, zero-row receipts block late creation, and legacy rows remain excluded.
3. Strict-RPC fixture slice: CTF state/identity/path proof across all four frozen adapter policy IDs.
4. Run targeted tests after each cycle and the full suite at every task boundary.
5. Run `python3 -m compileall -q src scripts`, `git diff --check`, and Markdown-link validation.

## Task 9 — Independent gates

Give fresh reviewers the approved design, ticket, base/head diff, and test output. Require PASS/FAIL
against every acceptance criterion, fix every actionable finding, and re-review changed safety code.

In a detached worktree at the exact reviewed SHA, independently mutate at least:

1. accept one provider or duplicate provider identity;
2. use higher head or fewer than five confirmations;
3. ignore block-hash disagreement;
4. trust Gamma token ordering without CTF position derivation;
5. swap slots or use a non-pUSD collateral;
6. omit condition-preparation adapter/question linkage;
7. make empty/incomplete/unsupported history CLEAR;
8. ignore reset, flag, emergency, or reverse precedence;
9. round a fractional payout;
10. omit/change one canonical terminal field or add wall clock;
11. permit immutable terminal overwrite;
12. partially settle target rows before a later conflict;
13. omit a zero-row receipt or allow post-receipt creation;
14. let a legacy mutator settle a canonical pending row;
15. acknowledge before target commit or use target NORMAL durability;
16. bypass restart recovery or deliver after unavailable partial recovery;
17. derive a new coordinate when re-polling an accepted terminal;
18. ignore changed adapter/question/audit evidence during recovery;
19. treat target conflict as retryable instead of persistently halting;
20. accept malformed JSON-RPC ID/quantity/hash/ABI/log data;
21. count fractional Forecast VOID as calibration evidence;
22. exclude Maker/Shadow SETTLED or ignore its deterministic mark/exact rational columns;
23. hide immutable disputed/manual terminal from target tail counters;
24. allow a halted store to accept or deliver.
25. let a non-Stub metadata provider silently create a legacy Forecast row;
26. continue to ACCEPT/evaluate/sign after a `ConditionAlreadyTerminal` race.
27. compute a fractional projection under ambient Decimal context or omit rational authority columns.
28. drop v1 `QuestionUpdated` from normalized terminal audit evidence or classify it CLEAR;
29. scan logs from genesis or outside the derived transition interval;
30. use a page larger than 10,000 blocks, skip/overlap a page, or rescan logs during terminal recovery.
31. classify a normal v1.0.1 resolution as CLEAR without historical OO disputer proof.
32. accept adapter/CTF terminal events from different transactions or reversed log order.
33. skip/change the frozen preceding-empty or exact-deployment-block nonempty code checks.

Each mutant must be killed by a named test for the intended reason. Restore byte-clean state and
rerun targeted and full suites.

## Task 10 — Final checkpoint

- Clean feature tree and exact SHA.
- Full suite, compile, link, and diff checks green with explicit counts.
- Independent specification and mutation reviews PASS with no survivors.
- No deployment, signing, chain write, or service activation.
- Post evidence to POL-15 if the connector is available.
- Do not merge or push until the owner approves the exact reviewed SHA.
