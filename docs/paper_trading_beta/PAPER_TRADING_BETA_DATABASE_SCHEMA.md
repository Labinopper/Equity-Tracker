# Paper Trading Beta Database Schema

Last updated: `2026-03-13`

Status: proposed SQLite-first schema for the exploratory paper-trading beta. This is a research and demo-trading schema, not a live-execution schema.

## 1. High-Level Schema Overview

Recommended persistence boundary:

- `portfolio.db`
  - Existing Equity Tracker source of truth for holdings, transactions, tax, deterministic settings, and core product state.
- `beta_research.db`
  - Paper-trading beta reference data, market/news facts, features, labels, score tape, experiments, governance records, and immutable demo-trade records.
- `beta_artifacts/`
  - Model files, replay bundles, exported reports, and archived research packs.

This split is recommended because the beta will be far more write-heavy and storage-heavy than the deterministic core. Minute bars, news rows, feature values, label values, and score tape should not bloat the same SQLite file that holds live portfolio truth if independent backup and restore matter.

Core design principles:

- keep raw facts separate from derived features, labels, predictions, execution, and evaluation;
- prefer append-only event tables for audit-critical workflows;
- use explicit version tables for strategies, models, features, labels, cost assumptions, and classifiers;
- mirror only the core reference data the beta needs;
- use JSON only for raw payloads, sparse explanations, and versioned configuration blobs.

### 1.1 Naming Convention

All beta tables use a `beta_` prefix and live in `beta_research.db`.

### 1.2 Core Separation

The schema keeps these layers distinct:

- raw facts
- derived features
- labels
- predictions
- recommendation and decision events
- simulated execution
- evaluation and attribution

That separation is required for replayability and for diagnosing whether failure came from prediction quality, trade expression, capital allocation, or market regime.

## 2. Table-by-Table Schema Proposal

### 2.1 Instrument and Reference Domain

#### `beta_instruments`

- Purpose: master record for tradeable equities, benchmarks, sector refs, FX pairs, and macro context series.
- Key columns: `id`, `core_security_id`, `symbol`, `display_name`, `instrument_type`, `instrument_role`, `exchange_id`, `currency_code`, `sector_id`, `industry_id`, `is_active`, `created_at`, `updated_at`
- Primary key: `id`
- Important foreign keys: `exchange_id -> beta_exchanges.id`, `sector_id -> beta_sectors.id`, `industry_id -> beta_industries.id`
- Important indexes: unique `(symbol, exchange_id, instrument_type)`, `(instrument_role, is_active)`, `(core_security_id)`
- Append-only vs mutable: mutable current-state master row
- Versioning notes: `core_security_id` is a soft reference back to `portfolio.db`

Suggested enums:

- `instrument_type`: `EQUITY`, `INDEX`, `SECTOR_PROXY`, `FX_PAIR`, `MACRO_SERIES`, `CASH`
- `instrument_role`: `TRADEABLE`, `BENCHMARK`, `SECTOR_REF`, `FX_CONTEXT`, `MACRO_CONTEXT`

#### `beta_instrument_aliases`

- Purpose: alternate names and ticker variants for deterministic news/entity linkage.
- Key columns: `id`, `instrument_id`, `alias_text`, `alias_type`, `priority`, `is_active`
- Primary key: `id`
- Important foreign keys: `instrument_id -> beta_instruments.id`
- Important indexes: unique `(instrument_id, alias_text)`, `(alias_text)`
- Append-only vs mutable: mutable
- Versioning notes: no special versioning; operational helper table

#### `beta_exchanges`

- Purpose: exchange metadata and default session hours.
- Key columns: `id`, `exchange_code`, `mic_code`, `display_name`, `timezone_name`, `regular_open_time`, `regular_close_time`, `country_code`
- Primary key: `id`
- Important indexes: unique `(exchange_code)`, unique `(mic_code)`
- Append-only vs mutable: mutable

#### `beta_trading_calendar_days`

- Purpose: market/session calendar by exchange and date.
- Key columns: `id`, `exchange_id`, `session_date`, `is_trading_day`, `session_type`, `open_at`, `close_at`, `notes`
- Primary key: `id`
- Important foreign keys: `exchange_id -> beta_exchanges.id`
- Important indexes: unique `(exchange_id, session_date)`, `(session_date)`
- Append-only vs mutable: mutable via upsert

#### `beta_sectors`

- Purpose: sector reference dictionary.
- Key columns: `id`, `sector_code`, `sector_name`
- Primary key: `id`
- Important indexes: unique `(sector_code)`
- Append-only vs mutable: mutable

#### `beta_industries`

- Purpose: industry dictionary linked to sectors.
- Key columns: `id`, `industry_code`, `industry_name`, `sector_id`
- Primary key: `id`
- Important foreign keys: `sector_id -> beta_sectors.id`
- Important indexes: unique `(industry_code)`, `(sector_id)`
- Append-only vs mutable: mutable

#### `beta_universes`

- Purpose: named universe definitions and frozen version metadata.
- Key columns: `id`, `universe_name`, `version_code`, `asset_class`, `base_currency`, `status`, `effective_from`, `effective_to`, `rule_text`, `created_by`, `created_at`
- Primary key: `id`
- Important indexes: unique `(universe_name, version_code)`, `(status)`
- Append-only vs mutable: immutable once used
- Versioning notes: new rule set means a new row

#### `beta_universe_membership_events`

- Purpose: append-only inclusion/exclusion history.
- Key columns: `id`, `universe_id`, `instrument_id`, `action`, `effective_at`, `reason_code`, `reason_text`, `reviewer_id`
- Primary key: `id`
- Important foreign keys: `universe_id -> beta_universes.id`, `instrument_id -> beta_instruments.id`
- Important indexes: `(universe_id, instrument_id, effective_at)`, `(instrument_id, effective_at)`
- Append-only vs mutable: append-only

#### `beta_benchmark_sets`

- Purpose: versioned benchmark framework definitions.
- Key columns: `id`, `name`, `version_code`, `description`, `base_currency`, `created_at`
- Primary key: `id`
- Important indexes: unique `(name, version_code)`
- Append-only vs mutable: immutable

#### `beta_benchmark_mappings`

- Purpose: instrument-to-benchmark mapping for market, sector, and optional FX context.
- Key columns: `id`, `benchmark_set_id`, `instrument_id`, `market_benchmark_instrument_id`, `sector_benchmark_instrument_id`, `fx_context_instrument_id`, `effective_from`, `effective_to`
- Primary key: `id`
- Important foreign keys: all instrument refs point to `beta_instruments.id`; `benchmark_set_id -> beta_benchmark_sets.id`
- Important indexes: unique `(benchmark_set_id, instrument_id, effective_from)`, `(instrument_id, effective_to)`
- Append-only vs mutable: append-only versioned mapping

### 2.2 Market Data Domain

The recommended bar design is shared bar tables rather than separate benchmark/sector/FX bar tables. Instrument role already distinguishes tradeable and context series.

#### `beta_data_ingestion_runs`

- Purpose: generic ingestion/build run ledger across market, news, and derived-build domains.
- Key columns: `id`, `domain`, `source_name`, `started_at`, `completed_at`, `status`, `requested_count`, `inserted_count`, `updated_count`, `error_count`, `notes_json`
- Primary key: `id`
- Important indexes: `(domain, started_at)`, `(status)`
- Append-only vs mutable: append-only

#### `beta_daily_bars`

- Purpose: daily OHLCV bars for equities, benchmarks, sector refs, and FX pairs.
- Key columns: `bar_id`, `instrument_id`, `bar_date`, `open`, `high`, `low`, `close`, `volume`, `provider`, `source_timestamp`, `ingested_at`, `adjustment_basis`, `revision_number`, `is_latest_revision`
- Primary key: `bar_id`
- Important foreign keys: `instrument_id -> beta_instruments.id`
- Important indexes: unique `(instrument_id, bar_date, provider, revision_number)`, `(instrument_id, bar_date, is_latest_revision)`, `(bar_date)`
- Append-only vs mutable: append new revision rows; do not overwrite prior revisions
- Versioning notes: revisions are row-level, not table-level

#### `beta_intraday_bars`

- Purpose: minute and derived intraday bars used for research and replay.
- Key columns: `bar_id`, `instrument_id`, `timeframe`, `bar_timestamp`, `open`, `high`, `low`, `close`, `volume`, `provider`, `source_timestamp`, `ingested_at`, `revision_number`, `is_latest_revision`
- Primary key: `bar_id`
- Important foreign keys: `instrument_id -> beta_instruments.id`
- Important indexes: unique `(instrument_id, timeframe, bar_timestamp, provider, revision_number)`, `(instrument_id, timeframe, bar_timestamp, is_latest_revision)`, `(timeframe, bar_timestamp)`
- Append-only vs mutable: append-only with revision rows

#### `beta_corporate_action_events`

- Purpose: splits, symbol changes, mergers, and dividend-related actions that affect adjustment logic.
- Key columns: `id`, `instrument_id`, `action_type`, `effective_date`, `provider_event_key`, `ratio_numerator`, `ratio_denominator`, `description`, `provider`, `source_timestamp`, `ingested_at`
- Primary key: `id`
- Important foreign keys: `instrument_id -> beta_instruments.id`
- Important indexes: unique `(provider, provider_event_key)`, `(instrument_id, effective_date)`
- Append-only vs mutable: append-only

#### `beta_event_calendar_events`

- Purpose: scheduled catalysts such as earnings, dividends, and macro events.
- Key columns: `id`, `instrument_id`, `event_type`, `event_at`, `expected_session`, `provider_event_key`, `provider`, `source_timestamp`, `ingested_at`, `payload_json`
- Primary key: `id`
- Important foreign keys: `instrument_id -> beta_instruments.id`
- Important indexes: unique `(provider, provider_event_key)`, `(instrument_id, event_at)`, `(event_type, event_at)`
- Append-only vs mutable: append-only

### 2.3 News Ingestion Domain

#### `beta_news_sources`

- Purpose: source identity, feed configuration, and source-type metadata.
- Key columns: `id`, `source_name`, `source_type`, `base_url`, `feed_url`, `default_reputation_tier`, `supports_full_text`, `requires_symbol_inference`, `is_active`
- Primary key: `id`
- Important indexes: unique `(source_name)`, `(source_type, is_active)`
- Append-only vs mutable: mutable

Suggested enums:

- `source_type`: `RSS`, `OFFICIAL_SITE`, `TWELVEDATA_PRESS_RELEASE`, `MANUAL`

#### `beta_news_source_ratings`

- Purpose: versioned source-reputation policy.
- Key columns: `id`, `source_id`, `version_code`, `factual_reliability`, `market_objectivity`, `operational_stability`, `default_learning_treatment`, `notes`, `created_at`
- Primary key: `id`
- Important foreign keys: `source_id -> beta_news_sources.id`
- Important indexes: unique `(source_id, version_code)`
- Append-only vs mutable: immutable per version

#### `beta_news_ingestion_runs`

- Purpose: feed-level ingestion run metadata for news pulls/parses.
- Key columns: `id`, `source_id`, `started_at`, `completed_at`, `http_status`, `item_count`, `new_item_count`, `parser_version`, `error_text`, `raw_response_checksum`
- Primary key: `id`
- Important foreign keys: `source_id -> beta_news_sources.id`
- Important indexes: `(source_id, started_at)`, `(completed_at)`
- Append-only vs mutable: append-only

#### `beta_news_articles`

- Purpose: raw article metadata and raw provider payload retention.
- Key columns: `id`, `source_id`, `vendor_article_id`, `canonical_url`, `headline`, `summary_text`, `published_at`, `first_seen_at`, `ingested_at`, `language_code`, `raw_payload_json`, `content_hash`, `source_rating_id`
- Primary key: `id`
- Important foreign keys: `source_id -> beta_news_sources.id`, `source_rating_id -> beta_news_source_ratings.id`
- Important indexes: unique `(source_id, vendor_article_id)`, unique `(canonical_url)`, `(published_at)`, `(first_seen_at)`, `(content_hash)`
- Append-only vs mutable: append-only
- Versioning notes: `first_seen_at` is the key replay field for what the system knew at the time

#### `beta_news_text_snapshots`

- Purpose: optional retained article text or HTML where rights allow it.
- Key columns: `id`, `article_id`, `snapshot_type`, `body_text`, `body_html`, `license_basis`, `captured_at`
- Primary key: `id`
- Important foreign keys: `article_id -> beta_news_articles.id`
- Important indexes: unique `(article_id, snapshot_type)`
- Append-only vs mutable: append-only

#### `beta_news_story_clusters`

- Purpose: deduplication and novelty clustering across repeated stories.
- Key columns: `id`, `cluster_key`, `primary_article_id`, `cluster_type`, `first_seen_at`, `latest_seen_at`, `article_count`
- Primary key: `id`
- Important foreign keys: `primary_article_id -> beta_news_articles.id`
- Important indexes: unique `(cluster_key)`, `(first_seen_at)`
- Append-only vs mutable: mutable aggregate row

#### `beta_news_article_links`

- Purpose: deterministic or inferred article-to-instrument linkage.
- Key columns: `id`, `article_id`, `instrument_id`, `linkage_method`, `linkage_confidence`, `is_primary_link`, `evidence_text`
- Primary key: `id`
- Important foreign keys: `article_id -> beta_news_articles.id`, `instrument_id -> beta_instruments.id`
- Important indexes: unique `(article_id, instrument_id, linkage_method)`, `(instrument_id, article_id)`, `(instrument_id, linkage_confidence)`
- Append-only vs mutable: append-only

#### `beta_news_classifier_versions`

- Purpose: version registry for rules/models that classify article type, novelty, direction, and learning treatment.
- Key columns: `id`, `classifier_name`, `version_code`, `classifier_type`, `config_json`, `created_at`
- Primary key: `id`
- Important indexes: unique `(classifier_name, version_code)`
- Append-only vs mutable: immutable

#### `beta_news_article_classifications`

- Purpose: structured article judgments for market relevance, event category, novelty, and directional implication.
- Key columns: `id`, `article_id`, `classifier_version_id`, `cluster_id`, `novelty_status`, `article_type`, `event_category`, `direction_label`, `direction_confidence`, `market_relevance_score`, `substance_score`, `learning_treatment`, `is_actionable`, `reason_codes_json`, `classified_at`
- Primary key: `id`
- Important foreign keys: `article_id -> beta_news_articles.id`, `classifier_version_id -> beta_news_classifier_versions.id`, `cluster_id -> beta_news_story_clusters.id`
- Important indexes: unique `(article_id, classifier_version_id)`, `(event_category, direction_label)`, `(learning_treatment)`, `(novelty_status)`
- Append-only vs mutable: append-only

Suggested enums:

- `novelty_status`: `FIRST_REPORT`, `FOLLOW_UP`, `DUPLICATE`, `CORRECTION`
- `direction_label`: `POSITIVE`, `NEGATIVE`, `MIXED`, `UNCERTAIN`, `IRRELEVANT`
- `learning_treatment`: `INCLUDE`, `DOWNWEIGHT`, `CONTEXT_ONLY`, `EXCLUDE`

### 2.4 Feature Store Domain

#### `beta_feature_definitions`

- Purpose: versioned metadata for every reusable feature.
- Key columns: `id`, `feature_name`, `version_code`, `feature_family`, `source_domain`, `timeframe`, `lookback_spec`, `value_type`, `usage_status`, `is_active`, `definition_text`, `created_at`
- Primary key: `id`
- Important indexes: unique `(feature_name, version_code)`, `(feature_family, is_active)`
- Append-only vs mutable: immutable once used by a dataset, score run, or strategy

Suggested enums:

- `source_domain`: `PRICE`, `NEWS`, `FX`, `EVENT`, `REGIME`, `PORTFOLIO_CONTEXT`
- `usage_status`: `RESEARCH_ONLY`, `BACKTEST_APPROVED`, `LIVE_PAPER_APPROVED`

#### `beta_feature_sets`

- Purpose: grouped feature versions used together in a model or strategy.
- Key columns: `id`, `feature_set_name`, `version_code`, `target_domain`, `created_at`, `notes`
- Primary key: `id`
- Important indexes: unique `(feature_set_name, version_code)`
- Append-only vs mutable: immutable

#### `beta_feature_set_items`

- Purpose: membership of feature definitions inside a feature set.
- Key columns: `id`, `feature_set_id`, `feature_definition_id`, `is_required`, `sort_order`
- Primary key: `id`
- Important foreign keys: `feature_set_id -> beta_feature_sets.id`, `feature_definition_id -> beta_feature_definitions.id`
- Important indexes: unique `(feature_set_id, feature_definition_id)`
- Append-only vs mutable: immutable

#### `beta_feature_values`

- Purpose: reusable feature store values keyed by instrument and timestamp.
- Key columns: `feature_value_id`, `feature_definition_id`, `instrument_id`, `feature_timestamp`, `value_numeric`, `value_text`, `build_run_id`, `window_start`, `window_end`, `is_missing`, `created_at`
- Primary key: `feature_value_id`
- Important foreign keys: `feature_definition_id -> beta_feature_definitions.id`, `instrument_id -> beta_instruments.id`, `build_run_id -> beta_data_ingestion_runs.id`
- Important indexes: unique `(feature_definition_id, instrument_id, feature_timestamp)`, `(instrument_id, feature_timestamp)`, `(feature_definition_id, feature_timestamp)`
- Append-only vs mutable: append-only

#### `beta_feature_value_lineage`

- Purpose: provenance from feature values back to raw source rows where practical.
- Key columns: `id`, `feature_value_id`, `source_table`, `source_row_id`, `source_role`
- Primary key: `id`
- Important foreign keys: `feature_value_id -> beta_feature_values.feature_value_id`
- Important indexes: `(feature_value_id)`, `(source_table, source_row_id)`
- Append-only vs mutable: append-only

### 2.5 Label Store Domain

#### `beta_label_definitions`

- Purpose: versioned metadata for canonical and diagnostic labels.
- Key columns: `id`, `label_name`, `version_code`, `target_horizon`, `formula_text`, `benchmark_set_id`, `cost_model_version_id`, `fx_rule_text`, `is_canonical`, `created_at`
- Primary key: `id`
- Important foreign keys: `benchmark_set_id -> beta_benchmark_sets.id`, `cost_model_version_id -> beta_cost_model_versions.id`
- Important indexes: unique `(label_name, version_code)`, `(is_canonical)`
- Append-only vs mutable: immutable

#### `beta_label_values`

- Purpose: explicit stored outcomes rather than ad hoc recomputation.
- Key columns: `label_value_id`, `label_definition_id`, `instrument_id`, `decision_timestamp`, `horizon_end_at`, `return_local`, `return_gbp`, `return_fx_adjusted`, `market_return`, `sector_return`, `excess_return`, `cost_adjusted_return`, `target_before_stop`, `max_adverse_excursion`, `max_favorable_excursion`, `created_at`
- Primary key: `label_value_id`
- Important foreign keys: `label_definition_id -> beta_label_definitions.id`, `instrument_id -> beta_instruments.id`
- Important indexes: unique `(label_definition_id, instrument_id, decision_timestamp)`, `(instrument_id, decision_timestamp)`, `(label_definition_id, decision_timestamp)`
- Append-only vs mutable: append-only

#### `beta_label_value_lineage`

- Purpose: provenance from labels back to bars, benchmarks, FX series, and cost assumptions.
- Key columns: `id`, `label_value_id`, `source_table`, `source_row_id`, `source_role`
- Primary key: `id`
- Important foreign keys: `label_value_id -> beta_label_values.label_value_id`
- Important indexes: `(label_value_id)`
- Append-only vs mutable: append-only

### 2.6 Dataset, Experiment, and Hypothesis Domain

#### `beta_dataset_versions`

- Purpose: frozen dataset manifests for reproducible training/evaluation.
- Key columns: `id`, `dataset_name`, `version_code`, `feature_set_id`, `canonical_label_definition_id`, `universe_id`, `benchmark_set_id`, `built_at`, `build_notes`
- Primary key: `id`
- Important foreign keys: `feature_set_id -> beta_feature_sets.id`, `canonical_label_definition_id -> beta_label_definitions.id`, `universe_id -> beta_universes.id`, `benchmark_set_id -> beta_benchmark_sets.id`
- Important indexes: unique `(dataset_name, version_code)`
- Append-only vs mutable: immutable

#### `beta_dataset_rows`

- Purpose: dataset membership and split assignment by instrument and decision timestamp.
- Key columns: `row_id`, `dataset_version_id`, `instrument_id`, `decision_timestamp`, `split_label`, `fold_number`, `is_holdout`
- Primary key: `row_id`
- Important foreign keys: `dataset_version_id -> beta_dataset_versions.id`, `instrument_id -> beta_instruments.id`
- Important indexes: unique `(dataset_version_id, instrument_id, decision_timestamp)`, `(dataset_version_id, split_label, fold_number)`
- Append-only vs mutable: append-only

#### `beta_hypotheses`

- Purpose: research-hypothesis registry for the learning playground.
- Key columns: `id`, `hypothesis_code`, `title`, `description`, `owner_user_id`, `status`, `family`, `created_at`, `retired_at`
- Primary key: `id`
- Important indexes: unique `(hypothesis_code)`, `(status, family)`
- Append-only vs mutable: mutable status only via governance events

Suggested enum:

- `status`: `DRAFT`, `RESEARCH`, `PROMOTION_CANDIDATE`, `PROMOTED`, `SUSPENDED`, `RETIRED`, `REJECTED`

#### `beta_experiment_runs`

- Purpose: experiment registry to prevent undocumented drift and p-hacking.
- Key columns: `id`, `hypothesis_id`, `dataset_version_id`, `feature_set_id`, `label_definition_id`, `run_type`, `started_at`, `completed_at`, `acceptance_criteria_json`, `result_summary_json`
- Primary key: `id`
- Important foreign keys: `hypothesis_id -> beta_hypotheses.id`, `dataset_version_id -> beta_dataset_versions.id`, `feature_set_id -> beta_feature_sets.id`, `label_definition_id -> beta_label_definitions.id`
- Important indexes: `(hypothesis_id, started_at)`, `(dataset_version_id)`
- Append-only vs mutable: append-only

#### `beta_model_versions`

- Purpose: trained model registry with artifacts and confidence schema info.
- Key columns: `id`, `model_name`, `version_code`, `experiment_run_id`, `model_type`, `artifact_path`, `training_metrics_json`, `confidence_schema_version`, `created_at`
- Primary key: `id`
- Important foreign keys: `experiment_run_id -> beta_experiment_runs.id`
- Important indexes: unique `(model_name, version_code)`
- Append-only vs mutable: immutable

#### `beta_strategy_versions`

- Purpose: versioned strategy definitions tying together hypothesis, model, feature set, label target, cost model, and execution assumptions.
- Key columns: `id`, `strategy_name`, `version_code`, `hypothesis_id`, `model_version_id`, `feature_set_id`, `label_definition_id`, `cost_model_version_id`, `benchmark_set_id`, `execution_assumption_version_id`, `allocation_rule_json`, `status`, `effective_from`, `effective_to`
- Primary key: `id`
- Important foreign keys: `hypothesis_id -> beta_hypotheses.id`, `model_version_id -> beta_model_versions.id`, `feature_set_id -> beta_feature_sets.id`, `label_definition_id -> beta_label_definitions.id`, `cost_model_version_id -> beta_cost_model_versions.id`, `benchmark_set_id -> beta_benchmark_sets.id`, `execution_assumption_version_id -> beta_execution_assumption_versions.id`
- Important indexes: unique `(strategy_name, version_code)`, `(status, effective_from)`
- Append-only vs mutable: immutable after activation; status changes should be governed by events

#### `beta_promotion_decisions`

- Purpose: explicit governance of movement from learning playground to demo-trade lane.
- Key columns: `id`, `hypothesis_id`, `strategy_version_id`, `decision`, `decision_at`, `reviewer_id`, `reason_text`, `evidence_ref_json`
- Primary key: `id`
- Important foreign keys: `hypothesis_id -> beta_hypotheses.id`, `strategy_version_id -> beta_strategy_versions.id`
- Important indexes: `(hypothesis_id, decision_at)`, `(strategy_version_id, decision)`
- Append-only vs mutable: append-only

### 2.7 Prediction and Score Tape Domain

#### `beta_score_runs`

- Purpose: one scoring batch per strategy, timestamp, and mode.
- Key columns: `id`, `strategy_version_id`, `decision_timestamp`, `universe_id`, `feature_set_id`, `model_version_id`, `label_definition_id`, `run_mode`, `started_at`, `completed_at`, `coverage_count`
- Primary key: `id`
- Important foreign keys: `strategy_version_id -> beta_strategy_versions.id`, `universe_id -> beta_universes.id`, `feature_set_id -> beta_feature_sets.id`, `model_version_id -> beta_model_versions.id`, `label_definition_id -> beta_label_definitions.id`
- Important indexes: unique `(strategy_version_id, decision_timestamp, run_mode)`, `(decision_timestamp)`
- Append-only vs mutable: append-only

Suggested enum:

- `run_mode`: `BACKTEST`, `SHADOW`, `DEMO_LIVE`

#### `beta_score_tape`

- Purpose: full-universe scoring record for every eligible symbol at each decision timestamp.
- Key columns: `score_id`, `score_run_id`, `instrument_id`, `raw_prediction`, `predicted_return`, `predicted_probability`, `confidence_raw`, `confidence_normalized`, `confidence_band`, `confidence_schema_version`, `universe_rank`, `eligibility_status`, `recommendation_flag`, `rejection_reason_code`, `allocation_status`, `feature_set_id`, `feature_snapshot_ref`, `benchmark_mapping_id`, `created_at`
- Primary key: `score_id`
- Important foreign keys: `score_run_id -> beta_score_runs.id`, `instrument_id -> beta_instruments.id`, `feature_set_id -> beta_feature_sets.id`, `benchmark_mapping_id -> beta_benchmark_mappings.id`
- Important indexes: unique `(score_run_id, instrument_id)`, `(instrument_id, score_run_id)`, `(score_run_id, recommendation_flag, allocation_status)`, `(confidence_band)`
- Append-only vs mutable: append-only

Suggested enums:

- `eligibility_status`: `ELIGIBLE`, `NOT_ELIGIBLE`, `STALE_DATA`, `HALTED`, `OUT_OF_SESSION`
- `allocation_status`: `NOT_ELIGIBLE`, `ELIGIBLE_NOT_ALLOCATED`, `ALLOCATED`, `BLOCKED_BY_LIMIT`

#### `beta_score_explanations`

- Purpose: sparse explanation details and feature references for a score-tape row.
- Key columns: `id`, `score_id`, `top_feature_refs_json`, `eligibility_flags_json`, `explanation_text`, `score_breakdown_json`
- Primary key: `id`
- Important foreign keys: `score_id -> beta_score_tape.score_id`
- Important indexes: unique `(score_id)`
- Append-only vs mutable: append-only

### 2.7A Potential Signal and AI Audit Domain

#### `beta_signal_candidates`

- Purpose: persist potential signals as first-class research artifacts even when they are not promoted or traded.
- Key columns: `id`, `detected_at`, `instrument_id`, `signal_family`, `signal_name`, `source_domain`, `discovery_channel`, `detector_version`, `score_id`, `hypothesis_id`, `expected_horizon`, `expected_gain_estimate`, `direction_label`, `confidence_raw`, `candidate_status`, `rationale_text`, `created_at`
- Primary key: `id`
- Important foreign keys: `instrument_id -> beta_instruments.id`, `score_id -> beta_score_tape.score_id`, `hypothesis_id -> beta_hypotheses.id`
- Important indexes: `(instrument_id, detected_at)`, `(candidate_status, detected_at)`, `(signal_family, signal_name)`
- Append-only vs mutable: append-only candidate record; later status changes should be evented rather than overwritten

Suggested enums:

- `source_domain`: `PRICE`, `NEWS`, `FX`, `EVENT`, `MODEL_DISCOVERY`, `MANUAL_RESEARCH`
- `discovery_channel`: `RULE`, `MODEL`, `COMBINATION_MINER`, `NEWS_TAGGER`, `MANUAL`
- `candidate_status`: `DETECTED`, `UNDER_REVIEW`, `PROMOTED_TO_HYPOTHESIS`, `DISMISSED`, `MERGED_DUPLICATE`, `ARCHIVED`

#### `beta_signal_candidate_evidence`

- Purpose: explicit provenance for where a potential signal was found and which raw or derived artifacts supported it.
- Key columns: `id`, `signal_candidate_id`, `source_table`, `source_row_id`, `evidence_role`, `evidence_text`, `created_at`
- Primary key: `id`
- Important foreign keys: `signal_candidate_id -> beta_signal_candidates.id`
- Important indexes: `(signal_candidate_id)`, `(source_table, source_row_id)`
- Append-only vs mutable: append-only

#### `beta_signal_candidate_events`

- Purpose: lifecycle tracking for potential signals so later audits can see when a candidate was detected, reviewed, promoted, merged, or dismissed.
- Key columns: `id`, `signal_candidate_id`, `event_type`, `event_at`, `actor_type`, `actor_id`, `notes_json`
- Primary key: `id`
- Important foreign keys: `signal_candidate_id -> beta_signal_candidates.id`
- Important indexes: `(signal_candidate_id, event_at)`
- Append-only vs mutable: append-only

#### `beta_ai_review_runs`

- Purpose: persist structured AI or automated audit reviews so later queries can inspect what was reviewed, over what scope, and using which model/version.
- Key columns: `id`, `review_type`, `requested_at`, `requested_by`, `scope_json`, `strategy_version_id`, `dataset_version_id`, `status`, `review_model_name`, `review_model_version`, `summary_text`, `artifact_path`
- Primary key: `id`
- Important foreign keys: `strategy_version_id -> beta_strategy_versions.id`, `dataset_version_id -> beta_dataset_versions.id`
- Important indexes: `(review_type, requested_at)`, `(status, requested_at)`
- Append-only vs mutable: append-only review run record

Suggested enums:

- `review_type`: `POTENTIAL_GAINS_REVIEW`, `SIGNAL_AUDIT`, `PROMOTION_AUDIT`, `FAILURE_REVIEW`, `REGIME_REVIEW`
- `status`: `REQUESTED`, `RUNNING`, `COMPLETED`, `FAILED`, `CANCELLED`

#### `beta_ai_review_findings`

- Purpose: structured claims and verification results produced by a review run.
- Key columns: `id`, `review_run_id`, `finding_rank`, `target_table`, `target_id`, `claim_type`, `claim_text`, `verification_status`, `confidence`, `support_ref_json`
- Primary key: `id`
- Important foreign keys: `review_run_id -> beta_ai_review_runs.id`
- Important indexes: `(review_run_id, finding_rank)`, `(target_table, target_id)`
- Append-only vs mutable: append-only

### 2.8 Recommendation and Decision Domain

#### `beta_recommendations`

- Purpose: accepted recommendation records separated from the broader score tape.
- Key columns: `id`, `score_id`, `strategy_version_id`, `instrument_id`, `recommendation_status`, `decision_timestamp`, `expected_horizon_end`, `confidence_band`, `decision_explanation`, `estimated_friction`, `entry_rule_json`, `target_rule_json`, `stop_rule_json`
- Primary key: `id`
- Important foreign keys: `score_id -> beta_score_tape.score_id`, `strategy_version_id -> beta_strategy_versions.id`, `instrument_id -> beta_instruments.id`
- Important indexes: unique `(score_id)`, `(instrument_id, decision_timestamp)`
- Append-only vs mutable: append-only

#### `beta_decision_events`

- Purpose: lifecycle event trail for recommendation actions and state changes.
- Key columns: `id`, `recommendation_id`, `event_type`, `event_at`, `actor_type`, `actor_id`, `notes_json`
- Primary key: `id`
- Important foreign keys: `recommendation_id -> beta_recommendations.id`
- Important indexes: `(recommendation_id, event_at)`
- Append-only vs mutable: append-only

#### `beta_manual_override_events`

- Purpose: manual suppress/allow/priority/size intervention audit trail.
- Key columns: `id`, `score_id`, `recommendation_id`, `override_type`, `before_state_json`, `after_state_json`, `reason_code`, `reason_text`, `user_id`, `created_at`
- Primary key: `id`
- Important foreign keys: `score_id -> beta_score_tape.score_id`, `recommendation_id -> beta_recommendations.id`
- Important indexes: `(score_id)`, `(created_at)`
- Append-only vs mutable: append-only

### 2.9 Paper Execution and Demo-Trade Domain

#### `beta_trade_intents`

- Purpose: allocation decisions that transform accepted recommendations into intended demo-trade expressions.
- Key columns: `id`, `recommendation_id`, `allocation_status`, `intended_size_gbp`, `intended_units`, `priority_rank`, `created_at`
- Primary key: `id`
- Important foreign keys: `recommendation_id -> beta_recommendations.id`
- Important indexes: unique `(recommendation_id)`
- Append-only vs mutable: append-only

#### `beta_demo_positions`

- Purpose: convenience header row for current position state in the immutable demo-trade lane.
- Key columns: `id`, `trade_intent_id`, `instrument_id`, `position_status`, `opened_at`, `closed_at`, `base_currency`, `entry_fx_rate`, `exit_fx_rate`, `planned_horizon_end`
- Primary key: `id`
- Important foreign keys: `trade_intent_id -> beta_trade_intents.id`, `instrument_id -> beta_instruments.id`
- Important indexes: `(position_status, instrument_id)`, `(opened_at)`
- Append-only vs mutable: mutable convenience row backed by immutable events

#### `beta_demo_position_events`

- Purpose: immutable lifecycle of entries, exits, adjustments, and closures.
- Key columns: `id`, `position_id`, `event_type`, `event_at`, `event_sequence`, `quantity_delta`, `price_local`, `price_gbp`, `fx_rate`, `reason_code`, `notes_json`
- Primary key: `id`
- Important foreign keys: `position_id -> beta_demo_positions.id`
- Important indexes: unique `(position_id, event_sequence)`, `(position_id, event_at)`
- Append-only vs mutable: append-only

#### `beta_demo_fills`

- Purpose: simulated fill records tied to position events and execution assumptions.
- Key columns: `id`, `position_event_id`, `fill_assumption_version_id`, `bar_ref_table`, `bar_ref_id`, `fill_price_local`, `spread_cost`, `slippage_cost`, `commission_cost`, `fill_confidence`
- Primary key: `id`
- Important foreign keys: `position_event_id -> beta_demo_position_events.id`, `fill_assumption_version_id -> beta_execution_assumption_versions.id`
- Important indexes: `(position_event_id)`
- Append-only vs mutable: append-only

#### `beta_cash_ledger_entries`

- Purpose: paper cash ledger for fills, fees, and FX conversion.
- Key columns: `ledger_entry_id`, `position_id`, `entry_type`, `entry_at`, `amount_gbp`, `amount_local`, `currency_code`, `fx_rate`, `source_event_id`, `notes`
- Primary key: `ledger_entry_id`
- Important foreign keys: `position_id -> beta_demo_positions.id`
- Important indexes: `(entry_at)`, `(position_id, entry_at)`
- Append-only vs mutable: append-only

### 2.10 Cost, Benchmark, and Attribution Domain

#### `beta_cost_model_versions`

- Purpose: top-level version manifest for fee, spread, slippage, and FX cost schedules.
- Key columns: `id`, `name`, `version_code`, `description`, `created_at`
- Primary key: `id`
- Important indexes: unique `(name, version_code)`
- Append-only vs mutable: immutable

#### `beta_broker_fee_schedule_versions`

- Purpose: versioned broker fee schedules for supported equity paper trades.
- Key columns: `id`, `cost_model_version_id`, `broker_name`, `version_code`, `schedule_json`, `effective_from`
- Primary key: `id`
- Important foreign keys: `cost_model_version_id -> beta_cost_model_versions.id`
- Important indexes: unique `(broker_name, version_code)`
- Append-only vs mutable: immutable

#### `beta_fx_cost_schedule_versions`

- Purpose: versioned FX spread/commission/conversion assumptions.
- Key columns: `id`, `cost_model_version_id`, `version_code`, `schedule_json`, `effective_from`
- Primary key: `id`
- Important foreign keys: `cost_model_version_id -> beta_cost_model_versions.id`
- Important indexes: unique `(cost_model_version_id, version_code)`
- Append-only vs mutable: immutable

#### `beta_execution_assumption_versions`

- Purpose: versioned fill, spread, and slippage assumptions for simulated execution.
- Key columns: `id`, `name`, `version_code`, `assumption_json`, `created_at`
- Primary key: `id`
- Important indexes: unique `(name, version_code)`
- Append-only vs mutable: immutable

#### `beta_trade_cost_breakdowns`

- Purpose: realized simulated cost components for each position event.
- Key columns: `id`, `position_event_id`, `broker_fee_gbp`, `spread_cost_gbp`, `slippage_cost_gbp`, `fx_conversion_cost_gbp`, `total_cost_gbp`
- Primary key: `id`
- Important foreign keys: `position_event_id -> beta_demo_position_events.id`
- Important indexes: unique `(position_event_id)`
- Append-only vs mutable: append-only

#### `beta_return_attributions`

- Purpose: decomposition of realized outcome into benchmark, excess, FX, and cost components.
- Key columns: `id`, `position_id`, `label_definition_id`, `market_return`, `sector_return`, `instrument_excess_return`, `fx_effect`, `cost_drag`, `net_return`, `calculated_at`
- Primary key: `id`
- Important foreign keys: `position_id -> beta_demo_positions.id`, `label_definition_id -> beta_label_definitions.id`
- Important indexes: unique `(position_id, label_definition_id)`
- Append-only vs mutable: append-only

### 2.11 Governance and Audit Domain

#### `beta_event_log`

- Purpose: global append-only event log across beta entities for audit and replay support.
- Key columns: `id`, `entity_table`, `entity_id`, `action`, `actor_type`, `actor_id`, `old_values_json`, `new_values_json`, `created_at`
- Primary key: `id`
- Important indexes: `(entity_table, entity_id, created_at)`, `(created_at)`
- Append-only vs mutable: append-only

#### `beta_strategy_approval_events`

- Purpose: explicit strategy approvals, suspensions, retirements, and review sign-offs.
- Key columns: `id`, `strategy_version_id`, `approval_type`, `approved_by`, `approved_at`, `notes`
- Primary key: `id`
- Important foreign keys: `strategy_version_id -> beta_strategy_versions.id`
- Important indexes: `(strategy_version_id, approved_at)`
- Append-only vs mutable: append-only

#### `beta_configuration_versions`

- Purpose: frozen version snapshots for operational config broader than a single strategy.
- Key columns: `id`, `config_domain`, `version_code`, `config_json`, `created_at`
- Primary key: `id`
- Important indexes: unique `(config_domain, version_code)`
- Append-only vs mutable: immutable

#### `beta_feature_flags`

- Purpose: runtime flags controlling beta exposure or operational switches.
- Key columns: `id`, `flag_name`, `flag_value`, `effective_from`, `effective_to`, `updated_at`
- Primary key: `id`
- Important indexes: unique `(flag_name, effective_from)`
- Append-only vs mutable: slowly changing operational table

#### `beta_access_log`

- Purpose: access and administrative action log for beta surfaces.
- Key columns: `id`, `user_id`, `action_name`, `target_table`, `target_id`, `created_at`
- Primary key: `id`
- Important indexes: `(user_id, created_at)`, `(target_table, target_id)`
- Append-only vs mutable: append-only

### 2.12 Evaluation and Learning-Output Domain

These are materialized result tables, not primary raw facts.

#### `beta_evaluation_runs`

- Purpose: one evaluation batch across a defined strategy version and window.
- Key columns: `id`, `strategy_version_id`, `label_definition_id`, `started_at`, `completed_at`, `evaluation_window_start`, `evaluation_window_end`, `run_type`
- Primary key: `id`
- Important foreign keys: `strategy_version_id -> beta_strategy_versions.id`, `label_definition_id -> beta_label_definitions.id`
- Important indexes: `(strategy_version_id, completed_at)`
- Append-only vs mutable: append-only

#### `beta_strategy_evaluation_summaries`

- Purpose: top-level strategy summary metrics for an evaluation run.
- Key columns: `id`, `evaluation_run_id`, `trades_count`, `paper_return`, `benchmark_relative_return`, `max_drawdown`, `profit_factor`, `expectancy`, `calibration_score`
- Primary key: `id`
- Important foreign keys: `evaluation_run_id -> beta_evaluation_runs.id`
- Important indexes: unique `(evaluation_run_id)`
- Append-only vs mutable: append-only summary table

#### `beta_calibration_bucket_summaries`

- Purpose: confidence-bucket calibration outputs for an evaluation run.
- Key columns: `id`, `evaluation_run_id`, `confidence_band`, `observation_count`, `avg_predicted`, `avg_realized`, `hit_rate`
- Primary key: `id`
- Important foreign keys: `evaluation_run_id -> beta_evaluation_runs.id`
- Important indexes: unique `(evaluation_run_id, confidence_band)`
- Append-only vs mutable: append-only summary table

#### `beta_signal_performance_summaries`

- Purpose: per-feature or per-signal performance outputs.
- Key columns: `id`, `evaluation_run_id`, `feature_definition_id`, `signal_bucket`, `observation_count`, `avg_excess_return`, `cost_adjusted_return`, `notes_json`
- Primary key: `id`
- Important foreign keys: `evaluation_run_id -> beta_evaluation_runs.id`, `feature_definition_id -> beta_feature_definitions.id`
- Important indexes: `(evaluation_run_id, feature_definition_id)`
- Append-only vs mutable: append-only summary table

#### `beta_regime_scorecards`

- Purpose: regime-specific summary outputs.
- Key columns: `id`, `evaluation_run_id`, `regime_key`, `observation_count`, `avg_excess_return`, `drawdown`, `hit_rate`
- Primary key: `id`
- Important foreign keys: `evaluation_run_id -> beta_evaluation_runs.id`
- Important indexes: unique `(evaluation_run_id, regime_key)`
- Append-only vs mutable: append-only summary table

#### `beta_rejection_reason_summaries`

- Purpose: summary analytics of why candidates were rejected and what happened afterward.
- Key columns: `id`, `evaluation_run_id`, `rejection_reason_code`, `count`, `avg_subsequent_label`
- Primary key: `id`
- Important foreign keys: `evaluation_run_id -> beta_evaluation_runs.id`
- Important indexes: unique `(evaluation_run_id, rejection_reason_code)`
- Append-only vs mutable: append-only summary table

#### `beta_hypothesis_leaderboard_snapshots`

- Purpose: snapshot ranking of promotion candidates in the learning playground.
- Key columns: `id`, `snapshot_at`, `hypothesis_id`, `rank_order`, `score`, `status`, `evidence_summary`
- Primary key: `id`
- Important foreign keys: `hypothesis_id -> beta_hypotheses.id`
- Important indexes: `(snapshot_at, rank_order)`
- Append-only vs mutable: append-only snapshot table

#### `beta_failure_clusters`

- Purpose: grouped failure-mode summaries for later review.
- Key columns: `id`, `evaluation_run_id`, `cluster_key`, `cluster_type`, `observation_count`, `summary_text`, `member_ref_json`
- Primary key: `id`
- Important foreign keys: `evaluation_run_id -> beta_evaluation_runs.id`
- Important indexes: `(evaluation_run_id, cluster_type)`
- Append-only vs mutable: append-only summary table

## 3. Recommended Primary Keys / Foreign Keys / Indexes

Primary key pattern:

- Use `INTEGER PRIMARY KEY` for high-volume tables such as bars, feature values, label values, score tape, position events, and cash ledger entries.
- Use stable `TEXT` ids only for lower-volume definition/governance tables if that better matches the rest of the application.

Foreign-key pattern:

- Keep hard FKs only within `beta_research.db`.
- Do not create hard FKs to `portfolio.db`.
- Where cross-system linkage is needed, use soft-reference fields such as `core_security_id`.

Index priorities for SQLite:

- time series: `(instrument_id, bar_date)`, `(instrument_id, timeframe, bar_timestamp)`
- features: `(feature_definition_id, instrument_id, feature_timestamp)`
- labels: `(label_definition_id, instrument_id, decision_timestamp)`
- score tape: `(score_run_id, instrument_id)`, `(instrument_id, score_run_id)`
- trade lifecycle: `(instrument_id, decision_timestamp)`, `(position_id, event_at)`, `(entry_at)`

## 4. Append-Only vs Mutable Guidance

Append-only immutable tables:

- ingestion runs
- universe membership events
- bars with revision rows
- corporate actions
- event calendar events
- news articles
- news links
- news classifications
- feature values
- label values
- dataset versions and dataset rows
- experiment runs
- model versions
- score runs
- score tape
- signal candidates
- signal-candidate evidence and lifecycle events
- recommendations
- decision events
- manual overrides
- trade intents
- demo position events
- fills
- cash ledger entries
- trade cost breakdowns
- return attributions
- AI review runs and findings
- event log
- strategy approvals
- evaluation runs
- evaluation summary/snapshot tables

Slowly changing or versioned tables:

- instruments
- exchanges
- trading calendar days
- news sources
- universes
- benchmark sets
- benchmark mappings
- feature definitions
- feature sets
- label definitions
- hypotheses
- strategy versions
- configuration versions
- feature flags

Mutable convenience tables:

- `beta_demo_positions`
- `beta_news_story_clusters`

## 5. Versioning Strategy

Every versioned design object should be explicit:

- feature definitions
- feature sets
- label definitions
- news classifier versions
- model versions
- strategy versions
- benchmark sets
- benchmark mappings
- cost model versions
- execution assumption versions
- configuration versions

Rules:

- once a version is used in a score run, label build, dataset build, recommendation, or demo trade, it is immutable;
- material changes create new rows, not silent mutation;
- confidence versioning must be explicit in both the model registry and score tape;
- promotion, suspension, and retirement happen through append-only governance events.

## 6. SQLite-Specific Implementation Notes

- Enable WAL mode.
- Enable foreign keys explicitly.
- Batch inserts for bars, features, labels, and score tape.
- Keep raw payload JSON sparse and compress at the application layer if size becomes problematic.
- Avoid excessive JSON where relational lookup is needed often.
- Prefer summary/materialized evaluation tables for UI reads rather than repeatedly aggregating the score tape.
- If volume grows materially, archive older bar, feature, and score-tape ranges into attached archive databases rather than overcomplicating the base schema.

## 7. Suggested v1 Minimum Subset

Recommended first implementation subset:

- reference:
  - `beta_instruments`
  - `beta_exchanges`
  - `beta_trading_calendar_days`
  - `beta_universes`
  - `beta_universe_membership_events`
  - `beta_benchmark_sets`
  - `beta_benchmark_mappings`
- market and event facts:
  - `beta_data_ingestion_runs`
  - `beta_daily_bars`
  - `beta_intraday_bars`
  - `beta_corporate_action_events`
  - `beta_event_calendar_events`
- news:
  - `beta_news_sources`
  - `beta_news_ingestion_runs`
  - `beta_news_articles`
  - `beta_news_article_links`
  - `beta_news_classifier_versions`
  - `beta_news_article_classifications`
- features and labels:
  - `beta_feature_definitions`
  - `beta_feature_sets`
  - `beta_feature_set_items`
  - `beta_feature_values`
  - `beta_label_definitions`
  - `beta_label_values`
- research registry:
  - `beta_dataset_versions`
  - `beta_dataset_rows`
  - `beta_hypotheses`
  - `beta_experiment_runs`
  - `beta_model_versions`
  - `beta_strategy_versions`
  - `beta_promotion_decisions`
- scoring and recommendations:
  - `beta_score_runs`
  - `beta_score_tape`
  - `beta_score_explanations`
  - `beta_signal_candidates`
  - `beta_signal_candidate_evidence`
  - `beta_signal_candidate_events`
  - `beta_recommendations`
  - `beta_manual_override_events`
- demo-trade lane:
  - `beta_trade_intents`
  - `beta_demo_positions`
  - `beta_demo_position_events`
  - `beta_demo_fills`
  - `beta_cash_ledger_entries`
- cost and evaluation:
  - `beta_cost_model_versions`
  - `beta_execution_assumption_versions`
  - `beta_trade_cost_breakdowns`
  - `beta_return_attributions`
  - `beta_evaluation_runs`
  - `beta_strategy_evaluation_summaries`
  - `beta_calibration_bucket_summaries`
- audit:
  - `beta_ai_review_runs`
  - `beta_ai_review_findings`
  - `beta_event_log`

## 8. Suggested Future Expansion Path

Later expansions can add:

- richer feature and label lineage
- more materialized evaluation/report tables
- fuller replay-pack references stored in `beta_artifacts/`
- optional FX promotion from context-only to tradeable research universe without redesigning the schema
- archive/retention tooling once score tape and bar history become materially large

The main design rule should remain stable:

- raw facts first
- derived features second
- labels explicit
- predictions explicit
- execution separate
- evaluation separate
- governance append-only
