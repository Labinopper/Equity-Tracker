# Paper Trading Beta Docs

Last updated: `2026-03-18`

This folder now separates live beta documentation from historical design notes and generated evidence. The older March 14-15 documents are still useful context, but they are no longer the best source of truth for how the beta currently behaves.

## Start Here

- `docs/paper_trading_beta/CURRENT_STATE_2026-03-18.md`
  - Current implementation and database-validated runtime state as of `2026-03-18`.
- `docs/paper_trading_beta/REVIEW_2026-03-18.md`
  - Review findings, operational gaps, and the highest-value improvements identified in the current code and DB state.
- `docs/paper_trading_beta/CHANGES_2026-03-16.md`
  - Point-in-time implementation notes for the intraday bar fetch and backfill changes made on `2026-03-16`.

## Current Source Of Truth

Use these in order:

1. The code under `equity_tracker/src/beta/` and the beta UI/router under `equity_tracker/src/api/routers/paper_trading_beta.py`
2. `docs/paper_trading_beta/CURRENT_STATE_2026-03-18.md`
3. Generated benchmark and deep-dive evidence in this folder
4. Historical design/proposal docs in this folder

## Live Storage Boundary

Current observed layout:

- `data/portfolio.db`
  - Core Equity Tracker source of truth.
- `C:\EquityTrackerData\portfolio.beta_research.db`
  - Live beta research/runtime database currently used by the detached supervisor.
- `C:\EquityTrackerData\portfolio.beta_research.db.settings.json`
  - Live beta runtime settings.
- `data/beta_supervisor.lock`
  - Detached supervisor lock file.

## Current Docs

- `docs/paper_trading_beta/CURRENT_STATE_2026-03-18.md`
- `docs/paper_trading_beta/REVIEW_2026-03-18.md`
- `docs/paper_trading_beta/NEXT_STEP_PROPOSAL_2026-03-18.md`
- `docs/paper_trading_beta/CHANGES_2026-03-16.md`
- `docs/paper_trading_beta/DEEP_DIVE_EXHAUSTION_REVERSAL_5e462c20.md`
- `docs/paper_trading_beta/BETA_DB_BENCHMARK_*.md`

## Historical Design Docs

These are still useful background, but they should be read as proposal/history unless explicitly updated:

- `docs/paper_trading_beta/PAPER_TRADING_BETA_STRATEGY.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_RUNTIME_ARCHITECTURE.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_TECHNICAL_IMPLEMENTATION_PLAN.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_DATABASE_SCHEMA.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_REMEDIATION_STRATEGY_2026-03-14.md`
- `docs/paper_trading_beta/PAPER_TRADING_BETA_HYPOTHESIS_ENGINE_EVOLUTION_2026-03-14.md`
- `docs/paper_trading_beta/HYPOTHESIS_DISCOVERY_IMPLEMENTATION_NOTES_2026-03-15.md`
- `docs/paper_trading_beta/INTRADAY_EXECUTION_LAYER_IMPLEMENTATION_NOTES_2026-03-15.md`
- `docs/paper_trading_beta/deep-research-report.md`

## Generated Evidence

The benchmark markdown files in this folder are generated evidence snapshots, not architecture docs. Keep them for auditability, but do not treat them as current design documentation.
