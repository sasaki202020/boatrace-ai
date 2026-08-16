# Feature Forward V1 Source Audit

## Decision

FEATURE_COLLECTION_BLOCKED_SOURCE

## Sources

| Source | Existing implementation | Automated use authorization | Timestamp contract | Decision |
|---|---|---|---|---|
| Official beforeinfo page | official_fetcher + beforeinfo_parser | UNVERIFIED | fetchedAt is local fetch time; page update lacks full timezone proof | Disabled |
| Official racelist deadline | racelist_parser | UNVERIFIED | first HH:MM extraction is not independently verified | Disabled |
| Local approved snapshot inbox | collector implemented | No approved producer registered | Strict UTC/JST/deadline/clock contract | Waiting |
| strict-lag local history | existing as-of artifacts | Internal read only | Historical capture timestamp incomplete | Not collected as new forward source |

The existing Boatrace_BeforeInfo task is disabled and was not enabled. No official-site request was made.

## GitHub transport

GitHubContentsTransport is reusable in principle, but the approved allowlist covers existing synthetic/prospective paths, not anchors/features. No feature anchor write was attempted. externalTimestampVerified remains false.

## Blocker

A rights-backed producer contract for a pre-race snapshot source is missing. It must explicitly define automated collection, numeric/raw storage, request limits, and source timestamp semantics.
