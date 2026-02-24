# Codex Scope Audit - Equity Tracker (Living Ledger)

Last updated: 2026-02-24

This document tracks architectural audit findings over time.
Primary status authority is:
1. `PROJECT_STATUS.md` (high level)
2. `PROJECT_REFERENCE.md` (detailed)

## Original Audit Intent (2026-02-24)
- Validate implementation against project intent.
- Identify gaps in true-cost modelling, disposal simulation, tax outputs, and UI consistency.
- Prioritize fixes by impact and execution risk.

## Status Snapshot

Resolved since original audit:
- Broker fees are now applied in disposal simulation/commit gain paths.
- Simulate MAX excludes locked/unsellable lots.
- Simulate/commit UI uses stored true cost (not live recomputation) in current flow.
- RSU vest date capture and sell-lock behavior are implemented.
- ESPP+ early-exit summary separates cash, non-cash forfeiture, and economic outcome.
- Settings includes a DB reset (nuke/recreate) workflow.

Still open:
- Non-CGT pages still carry legacy `cgt` field naming in internals.
- Remaining mojibake literals on some templates.
- FX remains mostly USD->GBP focused.
- SIP-like 3-5 year NIC gap finding is superseded by clarified policy (NI not due after 3 years in this model).
- TemplateResponse deprecation finding is now resolved (request-first migration shipped in `v2.0.2`).
- ESPP+ dual-lot atomicity finding is now resolved (transactional pair write shipped in `v1.10.0`).

## Open Findings (Evidence-Based)

| Severity | Finding | Evidence | Why it matters | Next step |
|---|---|---|---|---|
| P1 | Non-CGT pages still use legacy `cgt` naming in data fields | `portfolio_service.py` uses `est_cgt_gbp`, `est_total_cgt_liability_gbp`; templates display these in employment-tax context | Conceptual drift and developer confusion | Introduce employment-tax-native field names/aliases for non-CGT flows |
| P1 | Mojibake still present in templates | Corrupted literals remain in `templates/portfolio.html` and `templates/add_lot.html` | UX trust/readability issue | Replace bad literals with clean UTF-8 or entities and add assertions |
| P2 | FX generalization incomplete | Conversion path is primarily USD->GBP | Inaccurate valuation for other currencies | Generalize FX pair handling and explicit missing-rate errors |

## Prioritized Backlog (Current)

### P1
1. Rename non-CGT `cgt` internal fields/labels to employment-tax-safe names.
2. Remove remaining mojibake from templates.

### P2
1. Expand FX conversion beyond USD.
2. Reduce scheme enum rigidity ahead of configurable schemes.

## Structural Risks
- Naming drift risk: domain terms in code and UI can diverge without explicit model vocabulary.
- Transaction integrity risk: multi-step writes for single business event.
- Tax-engine coupling risk: small rule changes can regress cross-page outputs.
- Currency architecture risk: current path assumes a narrow FX setup.

## Suggested Next Milestones
1. Milestone A: terminology + encoding cleanup.
2. Milestone B: FX generalization.
3. Milestone C: scheme configuration groundwork and grant workflow deepening.

## Paste Block for PROJECT_STATUS.md
### Codex Architectural Audit - 2026-02-24 (Refreshed)
- Core P0 disposal-fee gap is closed; broker fees now flow through gain calculations.
- Main active risks are terminology drift (`cgt` naming on non-CGT pages), template encoding cleanup, and FX generalization.
- Additional technical debt remains in FX generalization.
- Recommended sequence: (1) naming/encoding cleanup, (2) FX generalization, (3) terminology hardening.
