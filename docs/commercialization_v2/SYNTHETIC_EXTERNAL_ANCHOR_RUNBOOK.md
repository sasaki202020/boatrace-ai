# Synthetic External Anchor Runbook

This runbook covers one synthetic package only. Do not use a real date, race ID, racer ID, input file or prediction.

## Before approval

1. Confirm candidate and schema hashes against the frozen manifest.
2. Generate a synthetic package with obviously synthetic identifiers.
3. Confirm deterministic package SHA-256.
4. Generate a fresh random salt of at least 32 bytes.
5. Verify the commitment locally.
6. Display the canonical public anchor JSON and confirm it contains no salt, prediction, input, racer, odds or result.
7. Fill the exact dedicated repository and allowlist in the approval manifest.
8. Confirm the synthetic cutoff and ensure no prospective counter will use this package.
9. Run `prepare_day1_readiness_v2.py --audit-only` and confirm that network writes remain zero.
10. Confirm `status=DRY_RUN` and `networkRequests=0`.

## Human approval

The human reviewer checks the payload, repository, token scope, retention and edit/delete policy. Only that reviewer may set `humanApproved=true` and `publishAllowed=true`. This task leaves both false.

## Authorized one-Issue execution

The synthetic-only GitHub REST transport is implemented. It remains fail-closed until the approval manifest, exact repository allowlist, fine-grained credential, explicit `--publish-synthetic`, and exact confirmation token are all present.

After one Issue is created:

1. Save provider `created_at`, issue ID, URL, repository, body hash and receipt hash.
2. Fetch the Issue body once and verify canonical equality.
3. Verify `created_at < cutoff`; equality is late.
4. Append the receipt without updating prediction rows.
5. After cutoff, produce a reveal bundle without raw input.
6. Recompute the commitment and verify the ledger chain.
7. Do not edit, comment on or delete the Issue.

Synthetic anchors never increase prospective days or races. `Prospective Timing Status` remains `NOT_STARTED`.
