# Paper Trading Beta Docs

Last updated: `2026-03-13`

This folder contains the exploratory documentation for the segregated paper-trading beta. These documents are intentionally separate from the deterministic core-product source-of-truth docs.

## Documents

- `docs/paper_trading_beta/PAPER_TRADING_BETA_STRATEGY.md`
  - Core strategy, operating boundary, learning architecture, governance, and first-release shape.
- `docs/paper_trading_beta/PAPER_TRADING_BETA_RUNTIME_ARCHITECTURE.md`
  - Operational runtime plan covering jobs, cadence, storage boundaries, and the broad-corpus versus narrow-live-universe split.
- `docs/paper_trading_beta/PAPER_TRADING_BETA_TECHNICAL_IMPLEMENTATION_PLAN.md`
  - Full engineering delivery plan covering package structure, migrations, runtime modes, kill switches, resource management, and phased implementation.
- `docs/paper_trading_beta/PAPER_TRADING_BETA_DATABASE_SCHEMA.md`
  - SQLite-first database design for the research, scoring, governance, and demo-trade system.

## Storage Boundary

Recommended persistence layout:

- `portfolio.db`
  - Existing Equity Tracker holdings, transactions, tax, deterministic portfolio state, and core product surfaces.
- `beta_research.db`
  - Paper-trading beta reference data, market/news facts, features, labels, score tape, experiments, governance, and demo-trade records.
- `beta_artifacts/`
  - Trained model files, replay bundles, exports, archived evaluation packs, and other large external artifacts.

The beta should mirror only the minimum instrument/reference context it needs from the core system. Cross-database links should be treated as soft references rather than hard foreign-key dependencies.
