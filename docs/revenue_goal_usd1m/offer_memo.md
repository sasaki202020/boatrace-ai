# Offer Memo: Public Data Reliability Sprint

## One-Line Offer

We harden public-data pipelines so operators can see what changed, what is missing, what is stale, and what is safe to report before bad data reaches customers or executives.

## Buyer

- Small data teams.
- Media and research operators.
- SaaS teams relying on public pages or partner feeds.
- Founder-led companies with brittle reporting automation.

## Pain

- Public data changes without notice.
- Missing data is silently treated as zero or failure.
- Reports cannot be replayed or audited.
- Dashboards look complete but hide stale inputs.
- AI pilots exist, but KPI tracking and operational trust are weak.

## Deliverables

- Data-source inventory.
- Missing-state taxonomy: missing, pending, unavailable, parse_error, stale, source_not_ready.
- Freshness and schema checks.
- Replayable daily/weekly report generation.
- Audit report in Markdown and JSON.
- Simple web or static dashboard when useful.
- Handoff document with run commands and acceptance checks.

## Pricing

- Diagnostic audit: USD 7,500 fixed fee.
- Implementation sprint: USD 15,000 fixed fee.
- Monitoring retainer: USD 2,500 per month.

## 14-Day Sprint Shape

- Day 1-2: source and artifact inventory.
- Day 3-5: checks for freshness, schema, and missing states.
- Day 6-9: reporting and dashboard outputs.
- Day 10-12: replay and failure-mode tests.
- Day 13-14: handoff, KPI scorecard, next-step proposal.

## Success Metrics

- At least 95% of expected daily artifacts classified.
- Zero silent conversion of missing data to loss, zero, or success.
- Reproducible command set for rerunning reports.
- Clear owner-facing status: ready, pending, source_not_ready, or failed.

## Compliance Boundary

No gambling advice, no regulated financial advice, no deceptive scraping, no spam, no collection of sensitive personal data, and no deployment or paid-account access without explicit approval.

