# Equity Tracker - Project Status

Last updated: 2026-02-24  
Current version: `v1.9.17`

## Source of Truth Policy
- `PROJECT_STATUS.md` is the high-level source of truth for current state, version, and roadmap.
- `PROJECT_REFERENCE.md` is the detailed technical source of truth.
- `todo.md` is execution tracking and test evidence.
- `docs/CODEX_SCOPE_AUDIT.md` is historical audit context, not primary status.

## Objective
Deliver a reliable local decision-support app for equity holdings:
- true cost visibility
- realistic sell-now cash visibility
- economic outcome visibility after scheme-specific tax/lock behavior

## Versioning Policy (SemVer)
- Format: `MAJOR.MINOR.PATCH`.
- `MAJOR`: breaking behavior/contracts/data assumptions.
- `MINOR`: user-visible feature delivery (roadmap step completion).
- `PATCH`: bugfix/documentation/testing/internal cleanup with no intentional feature scope expansion.
- Default cadence: ship roadmap items as minor releases; bundle small bugfixes as patches to avoid runaway version growth.

## Changelog (Skimmable)
| Version | Date | Summary | Why it matters |
|---|---|---|---|
| `v1.9.17` | 2026-02-24 | Daily ticker freshness upgraded with DB-backed per-refresh price snapshots and market-aware status messaging: stale/no-change warnings only surface during open market windows, while closed sessions show `market closed (opening in ...)` countdown hints | Distinguishes true intraday staleness risk from normal out-of-hours inactivity and gives explicit timing context for when live movement should resume |
| `v1.9.16` | 2026-02-24 | Portfolio security collapse state persistence fix: per-security hidden/shown lot sections are now remembered across refresh cycles | Prevents manual hide/show decisions from being reset by `Refresh Prices`, keeping the working view stable during repeated intraday refreshes |
| `v1.9.15` | 2026-02-24 | Portfolio homepage UX cleanup: removed `Securities` stat tile (retained the other seven), added per-security collapsible lot sections with single-line collapsed lot summaries, and expanded `Est. Net Proceeds` panel to surface top-level metrics (`Total Quantity`, `Cost Basis`, `True Cost`, `Market Value`, P&L, tax, net) | Reduces visual noise, allows faster scan/hide workflows per security, and aligns the proceeds breakdown with the same core portfolio metrics shown at top level |
| `v1.9.14` | 2026-02-24 | Daily price-move infrastructure added: refresh now backfills `price_history` to earliest lot acquisition date (when missing) and portfolio cards show per-ticker daily move badges (`Up/Down/Flat %` + GBP value impact) using latest vs prior stored close | Preserves decision context over time (not just latest tick), fixes late lot-entry history gaps, and surfaces an immediate per-holding daily move signal on the homepage |
| `v1.9.13` | 2026-02-24 | Portfolio summary liquidity metrics aligned to sellable-now semantics: top tile renamed to `Est. Net Liquidity (Sellable)`, value now sums sellable `Net If Sold Today`, and companion `Blocked/Restricted Value` tile added | Prevents locked/forfeiture-restricted value from inflating immediate liquidation expectations and keeps portfolio homepage aligned to executable cash outcomes |
| `v1.9.12` | 2026-02-24 | IA/navigation redesign contract documented: decision-engine tab model (`Decide`, `Liquidity`, `Schemes`, `Risk`, `Simulate`, `Advanced`) plus sellable-only liquidity metric semantics | Aligns future UI restructuring around decision quality (sell vs hold outcomes, structural timing, risk concentration) while preserving current tax/scheme calculation engines |
| `v1.9.11` | 2026-02-24 | Portfolio decision-table alignment fix: moved per-row `...` lot actions to an end-of-row `Actions` column and normalized lot-menu cell styling/positioning | Fixes visual divider misalignment under `Scheme` and keeps row boundaries/scan-lines consistent while preserving all lot actions |
| `v1.9.10` | 2026-02-24 | Portfolio lot table restructured into decision states (`Sell Today`, `Next Milestone`, `Long-Term 5+ Years`) with paired Net/Gain columns, concise `Status`, and human-readable `Notes` | Makes lot-level decisioning directly scannable by separating neutral snapshot data from structural-outcome scenarios without changing core tax/scheme engine logic |
| `v1.9.9` | 2026-02-24 | Add Lot now supports entry currency selection (`GBP`/`USD`) for price-based schemes, converts inputs to GBP at save-time, and persists original-currency + FX metadata on lots | Enables mixed-currency lot entry while keeping all reporting and decisioning consistently GBP-based |
| `v1.9.8` | 2026-02-24 | Per-scheme `Est. Net Liquidation` aggregation aligned to post-tax economic P&L totals (not gross market-value-derived cash totals) across schemes | Keeps per-scheme liquidation outcome aligned with decision-first economics, especially for RSU and other employment-tax-sensitive holdings |
| `v1.9.7` | 2026-02-24 | ESPP transfer FIFO allocation corrected: whole-share constraint now applies to requested transfer quantity while lot consumption follows strict raw FIFO sequence (including fractional head remainders) | Removes cases where transfer behavior looked non-FIFO by skipping fractional FIFO head quantities and keeps UI defaults aligned with transferable whole-share total (`floor(total FIFO qty)`) |
| `v1.9.6` | 2026-02-24 | ESPP transfer quantity default/validation fix: default now uses FIFO max whole-share quantity and no longer blocks valid whole-share requests when source remainders are fractional | Resolves transfer failure cases like `2.3` remaining with requested `2` while preserving FIFO and whole-share-only constraints |
| `v1.9.5` | 2026-02-24 | ESPP transfer workflow refinement: UI now presents ESPP as a scheme-level FIFO pool, defaults transfer quantity to max FIFO whole shares, and allows whole-share transfers from fractional remaining lots | Fixes blocked transfers like `2.3` remaining with requested `2` while preserving FIFO behavior and cleanly leaving fractional residuals |
| `v1.9.4` | 2026-02-24 | Added `Per Scheme` category/page with current-vs-historic scheme metrics, per-scheme summary tiles, and realised/unrealised economic visibility | Makes scheme-level decisioning explicit (open exposure + disposal history) and surfaces ESPP+ early-sale forfeiture impact in one place |
| `v1.9.3` | 2026-02-24 | Portfolio homepage summary-tile readability pass: value row is bottom-aligned across cards even when labels wrap | Keeps high-level metrics visually aligned and easier to scan without changing calculations or behavior |
| `v1.9.2` | 2026-02-24 | Tax-year band support extended through `2035-36` with published `2026-27` IT/NI/Student-Loan values and deterministic carry-forward for unpublished years | Removes `tax_year not found` failures for forward-looking reports/simulations while keeping explicit HMRC alignment where published |
| `v1.9.1` | 2026-02-24 | ESPP transfer workflow upgraded to editable whole-share FIFO quantity handling with per-lot broker splits/merges; RSU/ESPP+ transfers remain full-lot | Aligns custody transfers with FIFO reality while preserving non-disposal semantics and clearer lot continuity across partial and final transfers |
| `v1.9.0` | 2026-02-24 | Added live-data Validation Output Suite (shared service + `GET /admin/validation_report` text/json + `python -m app.validation_report` CLI + as-of/lot-limit support) | Provides deterministic, copy/paste-ready evidence for independent calculation verification with full intermediate variables, rules, inputs, and invariants |
| `v1.8.3` | 2026-02-24 | Portfolio quantity display normalized to 2dp (UI-only) | Improves scanability and consistency for fractional holdings while preserving backend precision |
| `v1.8.2` | 2026-02-24 | Portfolio sellability chip declutter: hide `Sellable`; suppress `Locked until` whenever `Forfeiture Risk` is shown | Reduces status noise and prevents mixed/contradictory lock messaging on ESPP+ rows |
| `v1.8.1` | 2026-02-24 | Portfolio decision-table polish (header alignment, ESPP+ row simplification, signal column repositioning, stacked statuses) | Improves scanability and keeps sell-decision cells cleaner without changing core logic |
| `v1.8.0` | 2026-02-24 | Portfolio decision-table refactor with ESPP+ grouped rows (`Paid + Match`) and decision-zone clarity | Home table is now a clearer sell-decision control surface without changing tax engine logic |
| `v1.7.3` | 2026-02-24 | Portfolio header action alignment + per-lot overflow actions menu (`...`) | Primary actions are consistently right-aligned and lot actions are cleaner with less table clutter |
| `v1.7.2` | 2026-02-24 | Home dashboard polish pass (refresh diagnostics, header controls, table readability) | Portfolio page is cleaner and easier to scan during day-to-day use |
| `v1.7.1` | 2026-02-24 | Hardened transfer rules for `RSU`/`ESPP`/`ESPP_PLUS` -> `BROKERAGE` | Transfer behavior now matches scheme lock/forfeiture/tax semantics and ISA boundary rules |
| `v1.7.0` | 2026-02-24 | `ISA` first-class across model/UI/service/reporting | ISA holdings now behave as tax-sheltered and are visible in normal workflows |
| `v1.6.0` | 2026-02-24 | Added decision columns (`Sellability Status`, `Sell Now Cash/Economic`) | Portfolio is directly actionable lot-by-lot |
| `v1.5.0` | 2026-02-24 | Added non-disposal transfer workflow to brokerage | Custody moves can be recorded without fake disposals |
| `v1.4.0` | 2026-02-24 | Added lot edit workflow with audit traceability | Incorrect entries can be corrected safely |
| `v1.3.0` | 2026-02-24 | Corrected badge semantics/wording | Reduced misinterpretation of lock/tax states |
| `v1.2.0` | 2026-02-24 | Fixed refresh reliability + diagnostics states | Better trust in freshness and refresh behavior |
| `v1.1.0` | 2026-02-24 | Fixed lot `Est. Net Proceeds` blank states | Core decision field no longer silently empty |
| `v1.0.0` | 2026-02-24 | Usable baseline before S1-S7 hardening | Initial functional baseline |

## Current Delivery Status
- Usability sequence `S1` through `S7` is implemented.
- `ISA` is first-class across model, add-lot UI, portfolio/net-value display, and reporting semantics.
- Latest regression: `450 passed, 3 skipped` (`python -m pytest -q`, 2026-02-24).
- Validation Output Suite targeted gates: `4 passed` (`python -m pytest tests/test_api/test_validation_report_api.py tests/test_services/test_validation_report_cli.py -q`, 2026-02-24).
- IA/navigation redesign baseline for a decision-engine UX is defined and approved for implementation planning (no tax/FIFO logic changes).

## Current In-Scope Capabilities
- Portfolio tracking in GBP with lot-level views.
- Scheme-aware lots in active UI: `RSU`, `ESPP`, `ESPP_PLUS`, `BROKERAGE`, `ISA`.
- Add Lot supports input currency selection (`GBP`/`USD`) for `ESPP`/`ESPP_PLUS`/`BROKERAGE`/`ISA`; values are converted and stored in GBP for reporting while original-currency + FX acquisition metadata is retained on the lot.
- Deterministic FIFO simulation and commit.
- Tax-year band support window:
  - published IT/NI/Student-Loan values through `2026-27`.
  - forward-support years `2027-28` through `2035-36` carry forward latest published values until HMRC confirms updates.
- Portfolio summary stat tiles keep metric values bottom-aligned so mixed label heights do not break horizontal scan alignment.
- Per-scheme analytics page (`/per-scheme`) with:
  - scheme-level summary tiles for current exposure, unrealised post-tax proxy outcome, realised economic result, and lifetime economic view.
  - current vs previous (historic disposal) table rows with financial values (`cost basis`, `true cost`, gross value/proceeds, tax/economic P&L).
  - `Est. Net Liquidation` reflects summed post-tax economic P&L outcomes for current lots (decision-first net outcome), not gross market-value cash totals.
  - ESPP+ explicit potential forfeiture value visibility for early-sell scenarios.
- Reliable portfolio refresh diagnostics (`idle/updating/success/error`, `last success`, `last error`, `next refresh`).
- Price refresh now backfills missing historical daily closes to earliest acquisition date per held security (stored in `price_history` with `source=yfinance_history`), so late lot entry still gets prior daily coverage.
- Portfolio security cards show a per-ticker daily move tracker (`Up/Down/Flat %` and quantity-weighted GBP move) from latest vs previous stored close.
- Daily ticker tracker now persists per-refresh displayed GBP price snapshots in DB and surfaces freshness context from that history (`Updated ... ago`, `No change ... (market open)`, or `market closed (opening in ...)`).
- Portfolio security cards are collapsible; collapsed state retains a one-line lot composition summary (lot count/row count/qty/cost/true-cost/market-value context).
- Portfolio security card hidden/show state is persisted per security in browser local storage and restored on page load/refresh.
- Portfolio top stats intentionally exclude the `Securities` tile and keep the seven financial decision tiles.
- `Est. Net Proceeds` dropdown now mirrors top-level decision context by including `Total Quantity`, `Total Cost Basis`, `Total True Cost`, `Gross Market Value`, both unrealised P&L views, and existing tax/net proceeds fields.
- Lot-level decision table is scenario-first:
  - Snapshot columns: `Date`, `Scheme`, `Status`, `Qty`, `Market Value`, `True Cost`.
  - Decision-state outcomes:
    - `Net If Sold Today` and `Gain If Sold Today`.
    - `Net If Held (Next Milestone)` and `Gain If Held`.
    - `Net If Long-Term (5+ Years)` and `Gain If Long-Term`.
  - `Notes` column surfaces compact structural context (`Locked until ...`, `Match preserved in ...`, `Next tax window in ...`, `Fully matured`).
- Portfolio summary tiles include sellability-aware liquidity surfaces:
  - `Est. Net Liquidity (Sellable)` sums only sellable `Net If Sold Today` outcomes.
  - `Blocked/Restricted Value` surfaces currently non-realizable value (locked holdings + forfeiture-restricted matched-share value).
- Portfolio status presentation is explicit:
  - primary state surfaces as `Sellable`, `Locked`, or `Forfeiture Risk`.
  - `Locked until` text is shown only when no forfeiture-risk badge is present.
- ESPP+ table grouping by purchase event:
  - one row for `ESPP+ (Paid + Match)` with paid/match quantity split and explicit match-effect handling (`INCLUDED`, `FORFEITED`, `LOCKED`, `NONE`).
- Lot correction workflow with audit trail.
- Non-disposal transfer workflow (`RSU`/`ESPP`/`ESPP_PLUS` -> `BROKERAGE`) with audit trail and scheme-specific guardrails:
  - `ESPP` transfer is scheme-level in UI (FIFO pool per security), supports editable quantity, defaults to max transferable FIFO whole shares, and enforces whole-share-only input.
  - `ESPP` whole-share transfers are allocated by strict FIFO lot order (raw quantities), including fractional FIFO head remainders before newer lots.
  - `ESPP` whole-share transfers are allowed when total FIFO remainder supports the request; fractional residuals remain in source lots as applicable.
  - `ESPP` partial transfers split custody into independent active lots (`ESPP` remainder + `BROKERAGE` transfer lot); later remainder transfer merges into the same broker lot for that source lot.
  - `RSU` transfer only after vest date and must use full remaining lot quantity.
  - `ESPP+` transfer from employee lot forfeits linked in-window matched lots and must use full remaining lot quantity.
  - `ESPP+` transfer marks transfer-time employment tax eligibility.
  - direct transfer to `ISA` is blocked (`dispose -> Add Lot` required).
- Canonical badge semantics:
  - `Forfeiture Risk`
  - `Pre-Vest Lock`
  - `Tax Window`
- `ISA` tax-sheltered behavior reflected in portfolio/net-value and excluded from taxable report totals.
- Validation Output Suite for deterministic verification from live DB data:
  - API: `GET /admin/validation_report?format=text|json&security_id=&as_of=&limit_lots=`
  - CLI: `python -m app.validation_report --format text|json --security <id|ticker> --as-of <ISO>`
  - includes metadata, tax/setting snapshots, market/FX inputs with timestamps, per-security recomputes, per-lot intermediate math, and invariant PASS/FAIL summary.

## IA / Navigation Baseline (Approved)
- Primary tabs are capped at six:
  - `Decide`
  - `Liquidity`
  - `Schemes`
  - `Risk`
  - `Simulate`
  - `Advanced`
- Core decision surface remains mandatory across primary decision pages:
  - `Net If Sold Today`
  - `Gain vs True Cost`
  - `Net If Held (Next Milestone)`
  - `Net If Long-Term (5+ Years)`
- Liquidity metric contract:
  - `Est. Net Liquidity` must sum only sellable positions (`Net If Sold Today` where disposal is currently possible).
  - Locked inventory and forfeited/non-realizable matched shares are excluded from liquidity totals.
  - Excluded value should be surfaced separately as blocked/restricted value.
- Primary surfaces should prioritize net outcomes, structural timing changes, and concentration/liquidity risk; accounting-detail fields remain accessible via advanced/audit views.

## Out of Scope (Current Phase)
- HMRC same-day / 30-day / Section 104 matching.
- Global auth/multi-user hardening and deployment packaging.
- Full broker import/reconciliation pipeline.
- Background server scheduler for refresh.

## Known Issues (High Level, Open)
1. UI polish debt remains outside recently touched flows:
   - inline style debt still present (`71` `style=` occurrences in templates)
   - residual mojibake/encoding artifacts still present (`28` matches across key UI/service/router files).
2. Starlette `TemplateResponse` request-first migration is not completed (deprecation warnings still present).
3. ESPP+ dual-lot add flow is still non-atomic (two write calls).
4. FX conversion remains effectively USD->GBP-centric.
5. SIP-like NIC treatment in specific 3-5 year cases remains a known model limitation.
6. Global privacy masking (`Hide values`) is not yet implemented.
7. ESPP+ transfer-time employment tax eligibility is currently notes/audit text only, not a structured tax event usable by reporting.

## Roadmap (Post S1-S7, Version Targets)
1. `v1.10.0` Implement global `Hide values` mode (mask monetary values app-wide, preserve percentages).
2. `v1.11.0` Complete UI polish debt reduction:
   - remove remaining inline styles
   - remove remaining mojibake/encoding artifacts
   - keep responsive and keyboard behavior intact.
3. `v1.11.1` Migrate `TemplateResponse` usage to request-first signature.
4. `v1.12.0` Make ESPP+ employee/matched lot creation atomic in one transactional service path.
5. `v1.13.0` Generalize FX handling beyond USD->GBP.
6. `v1.13.1` Fix SIP-like NIC handling gap for 3-5 year scenarios.
7. `v2.0.0` Implement approved IA/navigation redesign (`Decide`, `Liquidity`, `Schemes`, `Risk`, `Simulate`, `Advanced`) and enforce sellable-only `Est. Net Liquidity` semantics in primary UX surfaces.

## Portfolio Page Follow-On Opportunities
1. Add quick row filters (`All`, `Warnings`, `Locked`, `Forfeiture Risk`) to reduce scanning time on large holdings.
2. Add optional sort controls for scenario columns (`Net/Gain If Sold Today`, `Net/Gain If Held`, `Net/Gain If Long-Term`) with deterministic default order.
3. Add compact hover/expand formula breakdown for scenario cells (cash, tax, forfeiture components) to reduce context switching to Simulate.
4. Add persistent table preferences (column visibility/order + filter state) so repeated workflows stay stable.
5. Add one-click "focus mode" for decision columns on smaller laptop widths to reduce horizontal-scroll friction.

## Working Rules
- Keep this file concise and decision-focused.
- Keep changelog entries skimmable and append-only (newest first).
- Record implementation detail/contracts in `PROJECT_REFERENCE.md`.
- Keep `todo.md` synced with version and test evidence.
