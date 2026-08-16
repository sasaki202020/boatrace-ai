# Feature Forward Collection V1

## Status

FEATURE_COLLECTION_BLOCKED_SOURCE

The collector accepts only locally placed, explicitly approved pre-race snapshot envelopes. It has no HTTP, browser, odds, prediction, BUY, EV, betting, payment, production, live, strict, or prospective-ledger integration.

The source approval manifest is disabled until documentary permission and a source contract are registered. Do not set collectionEnabled to true based on this document alone.

## Runtime paths

- Inbox: C:\Users\goo10\競艇\boatrace-ai-mvp\data\research\feature_forward_v1\inbox
- Store: C:\Users\goo10\競艇\boatrace-ai-mvp\data\research\feature_forward_v1\store
- Status: C:\Users\goo10\競艇\boatrace-ai-mvp\reports\feature_forward_v1\latest_status.json

## Contract

Each JSON envelope contains one race, six boats, timezone-aware UTC/JST capture times, a timezone-aware deadline, clock drift, source identity, and groups A/B/C. Result, winner, finish, payout, refund, odds and equivalent Japanese result keys are rejected.

Raw envelopes and normalized records are append-only. SQLite UPDATE and DELETE triggers reject mutation. A ledger hash chain covers snapshots and boat/group records.

## Promotion

Each group remains FEATURE_SOURCE_NOT_READY until at least 30 forward days, 80% coverage, zero post-deadline records, zero result leakage, complete provenance, deterministic parsing, isolated schema drift, and explained major missingness.

Coverage is not inferred from captured snapshots. An approved schedule denominator must be registered before coverage can be calculated; until then `scheduledRaces` and `coverage` remain null.

No model training or feature connection occurs automatically.
