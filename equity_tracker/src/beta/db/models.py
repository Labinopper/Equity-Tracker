"""Initial ORM models for the separate paper-trading beta database."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _new_uuid() -> str:
    return str(uuid.uuid4())


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaBase(DeclarativeBase):
    pass


class BetaSchemaMeta(BetaBase):
    __tablename__ = "beta_schema_meta"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    schema_version: Mapped[str] = mapped_column(String(40), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaSystemStatus(BetaBase):
    __tablename__ = "beta_system_status"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    core_db_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    beta_db_path: Mapped[str] = mapped_column(String(500), nullable=False)
    runtime_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="FULL_INTERNAL_BETA")
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    web_ui_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    observation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    learning_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    shadow_scoring_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    demo_execution_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    filings_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    supervisor_status: Mapped[str] = mapped_column(String(30), nullable=False, default="stopped")
    supervisor_pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    latest_snapshot_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaJobRun(BetaBase):
    __tablename__ = "beta_job_runs"
    __table_args__ = (
        Index("ix_beta_job_runs_job_started", "job_name", "started_at"),
        Index("ix_beta_job_runs_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    job_name: Mapped[str] = mapped_column(String(100), nullable=False)
    job_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class BetaPipelineSnapshot(BetaBase):
    __tablename__ = "beta_pipeline_snapshots"
    __table_args__ = (
        Index("ix_beta_pipeline_snapshots_type_created", "snapshot_type", "created_at"),
        Index("ix_beta_pipeline_snapshots_status", "overall_status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    snapshot_type: Mapped[str] = mapped_column(String(40), nullable=False, default="SUPERVISOR_CYCLE")
    trigger_job_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    overall_status: Mapped[str] = mapped_column(String(20), nullable=False, default="BOOTSTRAPPING")
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    metrics_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaInstrument(BetaBase):
    __tablename__ = "beta_instruments"
    __table_args__ = (
        CheckConstraint("market IN ('UK','US','FX','OTHER')", name="ck_beta_instruments_market"),
        UniqueConstraint("symbol", "exchange", name="uq_beta_instruments_symbol_exchange"),
        Index("ix_beta_instruments_market_active", "market", "is_active"),
        Index("ix_beta_instruments_symbol", "symbol"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    core_security_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    benchmark_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sector_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    sector_label: Mapped[str | None] = mapped_column(String(80), nullable=True)
    metadata_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaUniverseMembership(BetaBase):
    __tablename__ = "beta_universe_membership"
    __table_args__ = (
        CheckConstraint(
            "status IN ('SEED','ACTIVE','DEFERRED','REMOVED')",
            name="ck_beta_universe_membership_status",
        ),
        Index("ix_beta_universe_membership_status", "status", "effective_from"),
        Index("ix_beta_universe_membership_instrument", "instrument_id", "effective_from"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    instrument_id: Mapped[str] = mapped_column(ForeignKey("beta_instruments.id", ondelete="CASCADE"), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    priority_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reason_code: Mapped[str | None] = mapped_column(String(50), nullable=True)
    reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    effective_from: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    effective_to: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaResearchRanking(BetaBase):
    __tablename__ = "beta_research_rankings"
    __table_args__ = (
        Index("ix_beta_research_rankings_scope_run_rank", "ranking_scope", "ranking_run_code", "rank_position"),
        Index("ix_beta_research_rankings_status_created", "selection_status", "created_at"),
        Index("ix_beta_research_rankings_symbol_created", "symbol", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    ranking_run_code: Mapped[str] = mapped_column(String(40), nullable=False)
    ranking_scope: Mapped[str] = mapped_column(String(40), nullable=False, default="UNIVERSE_SYNC")
    instrument_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="SET NULL"),
        nullable=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    exchange: Mapped[str | None] = mapped_column(String(20), nullable=True)
    market: Mapped[str | None] = mapped_column(String(20), nullable=True)
    sector_key: Mapped[str | None] = mapped_column(String(40), nullable=True)
    selection_status: Mapped[str] = mapped_column(String(20), nullable=False, default="DEFERRED")
    rank_position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ranking_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    data_readiness_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    catalyst_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    hypothesis_relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    quality_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    diversification_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaDailyBar(BetaBase):
    __tablename__ = "beta_daily_bars"
    __table_args__ = (
        UniqueConstraint("instrument_id", "bar_date", name="uq_beta_daily_bars_instrument_date"),
        Index("ix_beta_daily_bars_instrument_date", "instrument_id", "bar_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_price_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    close_price_native: Mapped[str | None] = mapped_column(String(30), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaBenchmarkBar(BetaBase):
    __tablename__ = "beta_benchmark_bars"
    __table_args__ = (
        UniqueConstraint("benchmark_key", "bar_date", name="uq_beta_benchmark_bars_key_date"),
        Index("ix_beta_benchmark_bars_key_date", "benchmark_key", "bar_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    benchmark_key: Mapped[str] = mapped_column(String(40), nullable=False)
    market: Mapped[str] = mapped_column(String(20), nullable=False)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    bar_date: Mapped[date] = mapped_column(Date, nullable=False)
    close_price_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    close_price_native: Mapped[str | None] = mapped_column(String(30), nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    source: Mapped[str] = mapped_column(String(80), nullable=False)
    source_fetched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaIntradaySnapshot(BetaBase):
    __tablename__ = "beta_intraday_snapshots"
    __table_args__ = (
        UniqueConstraint("instrument_id", "observed_at", name="uq_beta_intraday_snapshots_instrument_observed"),
        Index("ix_beta_intraday_snapshots_instrument_observed", "instrument_id", "observed_at"),
        Index("ix_beta_intraday_snapshots_price_date", "price_date", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    price_date: Mapped[date] = mapped_column(Date, nullable=False)
    price_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    price_native: Mapped[str | None] = mapped_column(String(30), nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    direction: Mapped[str | None] = mapped_column(String(20), nullable=True)
    percent_change: Mapped[str | None] = mapped_column(String(30), nullable=True)
    source: Mapped[str | None] = mapped_column(String(50), nullable=True)
    observed_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ingested_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaFeatureDefinition(BetaBase):
    __tablename__ = "beta_feature_definitions"
    __table_args__ = (
        UniqueConstraint("feature_name", "version_code", name="uq_beta_feature_definitions_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    feature_name: Mapped[str] = mapped_column(String(80), nullable=False)
    version_code: Mapped[str] = mapped_column(String(40), nullable=False)
    feature_family: Mapped[str] = mapped_column(String(40), nullable=False)
    timeframe: Mapped[str] = mapped_column(String(20), nullable=False, default="1D")
    definition_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaFeatureValue(BetaBase):
    __tablename__ = "beta_feature_values"
    __table_args__ = (
        UniqueConstraint(
            "feature_definition_id",
            "instrument_id",
            "feature_date",
            name="uq_beta_feature_values_definition_instrument_date",
        ),
        Index("ix_beta_feature_values_instrument_date", "instrument_id", "feature_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feature_definition_id: Mapped[str] = mapped_column(
        ForeignKey("beta_feature_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    feature_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaLabelDefinition(BetaBase):
    __tablename__ = "beta_label_definitions"
    __table_args__ = (
        UniqueConstraint("label_name", "version_code", name="uq_beta_label_definitions_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    label_name: Mapped[str] = mapped_column(String(80), nullable=False)
    version_code: Mapped[str] = mapped_column(String(40), nullable=False)
    horizon_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    definition_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_canonical: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaLabelValue(BetaBase):
    __tablename__ = "beta_label_values"
    __table_args__ = (
        UniqueConstraint(
            "label_definition_id",
            "instrument_id",
            "decision_date",
            name="uq_beta_label_values_definition_instrument_date",
        ),
        Index("ix_beta_label_values_instrument_date", "instrument_id", "decision_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    label_definition_id: Mapped[str] = mapped_column(
        ForeignKey("beta_label_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    horizon_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(80), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaDatasetVersion(BetaBase):
    __tablename__ = "beta_dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_name", "version_code", name="uq_beta_dataset_versions_name_version"),
        Index("ix_beta_dataset_versions_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    dataset_name: Mapped[str] = mapped_column(String(120), nullable=False)
    version_code: Mapped[str] = mapped_column(String(40), nullable=False)
    label_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_label_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    feature_names_json: Mapped[str] = mapped_column(Text, nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    train_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    train_date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    train_date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    validation_date_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    validation_date_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaDatasetRow(BetaBase):
    __tablename__ = "beta_dataset_rows"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id",
            "instrument_id",
            "decision_date",
            name="uq_beta_dataset_rows_dataset_instrument_date",
        ),
        Index("ix_beta_dataset_rows_dataset_split", "dataset_version_id", "split_label", "decision_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    dataset_version_id: Mapped[str] = mapped_column(
        ForeignKey("beta_dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    split_label: Mapped[str] = mapped_column(String(20), nullable=False)
    label_value_numeric: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaExperimentRun(BetaBase):
    __tablename__ = "beta_experiment_runs"
    __table_args__ = (
        Index("ix_beta_experiment_runs_created", "created_at"),
        Index("ix_beta_experiment_runs_status", "status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    experiment_name: Mapped[str] = mapped_column(String(120), nullable=False)
    dataset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    label_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_label_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCESS")
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaStrategyVersion(BetaBase):
    __tablename__ = "beta_strategy_versions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','CHALLENGER','ACTIVE','SUSPENDED','RETIRED')",
            name="ck_beta_strategy_versions_status",
        ),
        UniqueConstraint("strategy_name", "version_code", name="uq_beta_strategy_versions_name_version"),
        Index("ix_beta_strategy_versions_active", "is_active", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    strategy_name: Mapped[str] = mapped_column(String(120), nullable=False)
    version_code: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    label_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_label_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    min_confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.55)
    min_expected_edge_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.20)
    capital_weight_mode: Mapped[str] = mapped_column(String(40), nullable=False, default="CONFIDENCE_EDGE")
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaStrategyEvent(BetaBase):
    __tablename__ = "beta_strategy_events"
    __table_args__ = (
        Index("ix_beta_strategy_events_strategy", "strategy_version_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    strategy_version_id: Mapped[str] = mapped_column(
        ForeignKey("beta_strategy_versions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status_before: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_after: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaModelVersion(BetaBase):
    __tablename__ = "beta_model_versions"
    __table_args__ = (
        Index("ix_beta_model_versions_created", "created_at"),
        Index("ix_beta_model_versions_active", "is_active", "created_at"),
        UniqueConstraint("model_name", "version_code", name="uq_beta_model_versions_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    model_name: Mapped[str] = mapped_column(String(80), nullable=False)
    version_code: Mapped[str] = mapped_column(String(40), nullable=False)
    algorithm: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="TRAINED")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_challenger: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    dataset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    training_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_row_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    feature_names_json: Mapped[str] = mapped_column(Text, nullable=False)
    coefficients_json: Mapped[str] = mapped_column(Text, nullable=False)
    intercept_value: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    feature_means_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    feature_scales_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    train_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    validation_sign_accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaValidationRun(BetaBase):
    __tablename__ = "beta_validation_runs"
    __table_args__ = (
        Index("ix_beta_validation_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    validation_name: Mapped[str] = mapped_column(String(120), nullable=False)
    validation_method: Mapped[str] = mapped_column(String(40), nullable=False, default="WALK_FORWARD")
    dataset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    strategy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_strategy_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    window_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    train_window_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    validation_window_rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_validation_mae: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_validation_sign_accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    avg_validation_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaTrainingDecision(BetaBase):
    __tablename__ = "beta_training_decisions"
    __table_args__ = (
        Index("ix_beta_training_decisions_created", "created_at"),
        Index("ix_beta_training_decisions_status_reason", "status_code", "reason_code", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    decision_type: Mapped[str] = mapped_column(String(40), nullable=False, default="DAILY_TRAINING")
    status_code: Mapped[str] = mapped_column(String(30), nullable=False)
    reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    performed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    trained: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    validation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_validation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    training_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_rows: Mapped[int | None] = mapped_column(Integer, nullable=True)
    walkforward_window_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    validation_sign_accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    walkforward_validation_sign_accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaScoreRun(BetaBase):
    __tablename__ = "beta_score_runs"
    __table_args__ = (
        Index("ix_beta_score_runs_scored", "scored_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    run_type: Mapped[str] = mapped_column(String(40), nullable=False, default="HEURISTIC_DAILY")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCESS")
    strategy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_strategy_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class BetaScoreTape(BetaBase):
    __tablename__ = "beta_score_tape"
    __table_args__ = (
        UniqueConstraint("score_run_id", "instrument_id", name="uq_beta_score_tape_run_instrument"),
        CheckConstraint(
            "direction IN ('BULLISH','BEARISH','NEUTRAL','RISK_OFF')",
            name="ck_beta_score_tape_direction",
        ),
        Index("ix_beta_score_tape_instrument_scored", "instrument_id", "scored_at"),
        Index("ix_beta_score_tape_recommendation", "recommendation_flag", "scored_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    score_run_id: Mapped[str] = mapped_column(
        ForeignKey("beta_score_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    strategy_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_strategy_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    predicted_return_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_volatility_5d: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    expected_edge_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recommendation_flag: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    scored_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaLedgerState(BetaBase):
    __tablename__ = "beta_ledger_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    base_currency: Mapped[str] = mapped_column(String(3), nullable=False, default="GBP")
    starting_capital_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="10000.00")
    available_cash_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="10000.00")
    deployed_capital_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="0.00")
    realized_pnl_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="0.00")
    unrealized_pnl_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="0.00")
    total_equity_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="10000.00")
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaCashLedgerEntry(BetaBase):
    __tablename__ = "beta_cash_ledger_entries"
    __table_args__ = (
        Index("ix_beta_cash_ledger_entries_created", "created_at"),
        Index("ix_beta_cash_ledger_entries_position", "position_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    position_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_demo_positions.id", ondelete="SET NULL"),
        nullable=True,
    )
    entry_type: Mapped[str] = mapped_column(String(40), nullable=False)
    amount_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    balance_after_gbp: Mapped[str] = mapped_column(String(30), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaRiskControlState(BetaBase):
    __tablename__ = "beta_risk_control_state"
    __table_args__ = (
        CheckConstraint(
            "degradation_status IN ('NORMAL','PAUSED','RECOVERING')",
            name="ck_beta_risk_control_state_status",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    demo_entries_paused: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    pause_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    degradation_status: Mapped[str] = mapped_column(String(20), nullable=False, default="NORMAL")
    recent_closed_trades: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recent_win_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    recent_avg_pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    auto_paused_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_resumed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaAiReviewRun(BetaBase):
    __tablename__ = "beta_ai_review_runs"
    __table_args__ = (
        Index("ix_beta_ai_review_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    review_type: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCESS")
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaAiReviewFinding(BetaBase):
    __tablename__ = "beta_ai_review_findings"
    __table_args__ = (
        Index("ix_beta_ai_review_findings_run", "review_run_id"),
        Index("ix_beta_ai_review_findings_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    review_run_id: Mapped[str] = mapped_column(
        ForeignKey("beta_ai_review_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    finding_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    subject_symbol: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaNewsSource(BetaBase):
    __tablename__ = "beta_news_sources"
    __table_args__ = (
        UniqueConstraint("source_name", "feed_url", name="uq_beta_news_sources_name_url"),
        Index("ix_beta_news_sources_active", "is_active", "market"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    feed_url: Mapped[str] = mapped_column(String(500), nullable=False)
    market: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="RSS")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaNewsIngestionRun(BetaBase):
    __tablename__ = "beta_news_ingestion_runs"
    __table_args__ = (
        Index("ix_beta_news_ingestion_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_news_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCESS")
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    linked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaNewsArticle(BetaBase):
    __tablename__ = "beta_news_articles"
    __table_args__ = (
        UniqueConstraint("source_id", "article_guid", name="uq_beta_news_articles_source_guid"),
        Index("ix_beta_news_articles_published", "published_at"),
        Index("ix_beta_news_articles_sentiment", "sentiment_label", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_news_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    article_guid: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    sentiment_label: Mapped[str] = mapped_column(String(20), nullable=False, default="NEUTRAL")
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    relevance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    matched_symbols_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaNewsArticleLink(BetaBase):
    __tablename__ = "beta_news_article_links"
    __table_args__ = (
        UniqueConstraint("article_id", "instrument_id", name="uq_beta_news_article_links_article_instrument"),
        Index("ix_beta_news_article_links_instrument", "instrument_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    article_id: Mapped[str] = mapped_column(
        ForeignKey("beta_news_articles.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    linkage_method: Mapped[str] = mapped_column(String(40), nullable=False, default="SYMBOL_OR_NAME")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaFilingSource(BetaBase):
    __tablename__ = "beta_filing_sources"
    __table_args__ = (
        UniqueConstraint("source_name", "feed_url", name="uq_beta_filing_sources_name_url"),
        Index("ix_beta_filing_sources_active", "is_active", "market"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_name: Mapped[str] = mapped_column(String(120), nullable=False)
    feed_url: Mapped[str] = mapped_column(String(500), nullable=False)
    market: Mapped[str | None] = mapped_column(String(20), nullable=True)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False, default="OFFICIAL_FEED")
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaFilingIngestionRun(BetaBase):
    __tablename__ = "beta_filing_ingestion_runs"
    __table_args__ = (
        Index("ix_beta_filing_ingestion_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_filing_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCESS")
    fetched_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stored_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    linked_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaFilingEvent(BetaBase):
    __tablename__ = "beta_filing_events"
    __table_args__ = (
        UniqueConstraint("source_id", "event_guid", name="uq_beta_filing_events_source_guid"),
        CheckConstraint(
            "event_category IN ('OFFICIAL_RELEASE','REGULATORY_FILING','TRADING_UPDATE','EARNINGS','CORPORATE_ACTION','OTHER')",
            name="ck_beta_filing_events_category",
        ),
        CheckConstraint(
            "sentiment_label IN ('POSITIVE','NEGATIVE','NEUTRAL')",
            name="ck_beta_filing_events_sentiment",
        ),
        Index("ix_beta_filing_events_published", "published_at"),
        Index("ix_beta_filing_events_category", "event_category", "published_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    source_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_filing_sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    event_guid: Mapped[str] = mapped_column(String(500), nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    link_url: Mapped[str | None] = mapped_column(String(1000), nullable=True)
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    event_category: Mapped[str] = mapped_column(String(40), nullable=False, default="OTHER")
    sentiment_label: Mapped[str] = mapped_column(String(20), nullable=False, default="NEUTRAL")
    sentiment_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    importance_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    matched_symbols_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    keyword_tags_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_official: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaFilingEventLink(BetaBase):
    __tablename__ = "beta_filing_event_links"
    __table_args__ = (
        UniqueConstraint("event_id", "instrument_id", name="uq_beta_filing_event_links_event_instrument"),
        Index("ix_beta_filing_event_links_instrument", "instrument_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    event_id: Mapped[str] = mapped_column(
        ForeignKey("beta_filing_events.id", ondelete="CASCADE"),
        nullable=False,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    linkage_method: Mapped[str] = mapped_column(String(40), nullable=False, default="SYMBOL_OR_NAME")
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.5)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaEvaluationRun(BetaBase):
    __tablename__ = "beta_evaluation_runs"
    __table_args__ = (
        Index("ix_beta_evaluation_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    evaluation_type: Mapped[str] = mapped_column(String(60), nullable=False, default="LIVE_TRAILING")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="SUCCESS")
    summary_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaEvaluationSummary(BetaBase):
    __tablename__ = "beta_evaluation_summaries"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", name="uq_beta_evaluation_summaries_run"),
        CheckConstraint(
            "trend_label IN ('IMPROVING','STABLE','DECLINING')",
            name="ck_beta_evaluation_summaries_trend",
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("beta_evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    total_scores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommended_scores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommendation_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    labeled_scores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    open_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    closed_positions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    traded_candidates: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    conversion_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    win_rate_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_closed_pnl_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    realized_pnl_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="0.00")
    unrealized_pnl_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="0.00")
    avg_labeled_return_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    trend_label: Mapped[str] = mapped_column(String(20), nullable=False, default="STABLE")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaConfidenceBucketSummary(BetaBase):
    __tablename__ = "beta_confidence_bucket_summaries"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "bucket_label", name="uq_beta_conf_bucket_run_bucket"),
        CheckConstraint(
            "bucket_label IN ('LOW','MEDIUM','HIGH')",
            name="ck_beta_conf_bucket_label",
        ),
        Index("ix_beta_conf_bucket_run", "evaluation_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("beta_evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    bucket_label: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommendation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_expected_edge_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_future_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    alignment_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaDirectionSummary(BetaBase):
    __tablename__ = "beta_direction_summaries"
    __table_args__ = (
        UniqueConstraint("evaluation_run_id", "direction", name="uq_beta_direction_summaries_run_direction"),
        CheckConstraint(
            "direction IN ('BULLISH','BEARISH','NEUTRAL','RISK_OFF')",
            name="ck_beta_direction_summaries_direction",
        ),
        Index("ix_beta_direction_summaries_run", "evaluation_run_id"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    evaluation_run_id: Mapped[str] = mapped_column(
        ForeignKey("beta_evaluation_runs.id", ondelete="CASCADE"),
        nullable=False,
    )
    direction: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    recommendation_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    avg_confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_expected_edge_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    avg_future_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    alignment_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaHypothesis(BetaBase):
    __tablename__ = "beta_hypotheses"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','RESEARCH','PROMOTED','SUSPENDED','RETIRED','REJECTED')",
            name="ck_beta_hypotheses_status",
        ),
        Index("ix_beta_hypotheses_status", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="RESEARCH")
    evidence_score: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    auto_promoted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaHypothesisEvent(BetaBase):
    __tablename__ = "beta_hypothesis_events"
    __table_args__ = (
        Index("ix_beta_hypothesis_events_hypothesis", "hypothesis_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    hypothesis_id: Mapped[str] = mapped_column(
        ForeignKey("beta_hypotheses.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    status_before: Mapped[str | None] = mapped_column(String(20), nullable=True)
    status_after: Mapped[str | None] = mapped_column(String(20), nullable=True)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaHypothesisFamily(BetaBase):
    __tablename__ = "beta_hypothesis_families"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','PAUSED','ARCHIVED')",
            name="ck_beta_hypothesis_families_status",
        ),
        UniqueConstraint("family_code", name="uq_beta_hypothesis_families_code"),
        Index("ix_beta_hypothesis_families_status", "status", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    family_code: Mapped[str] = mapped_column(String(80), nullable=False)
    family_name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    generator_type: Mapped[str] = mapped_column(String(40), nullable=False, default="TEMPLATE")
    default_target_metric: Mapped[str] = mapped_column(String(80), nullable=False, default="fwd_5d_excess_return_pct")
    default_holding_period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    mutation_policy_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaHypothesisDefinition(BetaBase):
    __tablename__ = "beta_hypothesis_definitions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CANDIDATE','PROMISING','VALIDATED','DEGRADED','REJECTED','ARCHIVED')",
            name="ck_beta_hypothesis_definitions_status",
        ),
        CheckConstraint(
            "expected_direction IN ('BULLISH','BEARISH','NEUTRAL','RISK_OFF')",
            name="ck_beta_hypothesis_definitions_direction",
        ),
        UniqueConstraint("hypothesis_code", name="uq_beta_hypothesis_definitions_code"),
        Index("ix_beta_hypothesis_definitions_status", "status", "updated_at"),
        Index("ix_beta_hypothesis_definitions_family", "family_id", "updated_at"),
        Index("ix_beta_hypothesis_definitions_parent", "parent_hypothesis_id", "updated_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    family_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_families.id", ondelete="SET NULL"),
        nullable=True,
    )
    hypothesis_code: Mapped[str] = mapped_column(String(100), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    universe_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    entry_conditions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    exit_conditions_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    holding_period_days: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    target_metric: Mapped[str] = mapped_column(String(80), nullable=False, default="fwd_5d_excess_return_pct")
    expected_direction: Mapped[str] = mapped_column(String(20), nullable=False, default="BULLISH")
    feature_subset_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    parent_hypothesis_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    generation_source: Mapped[str] = mapped_column(String(40), nullable=False, default="TEMPLATE_SEED")
    provenance_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CANDIDATE")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaHypothesisTestRun(BetaBase):
    __tablename__ = "beta_hypothesis_test_runs"
    __table_args__ = (
        Index("ix_beta_hypothesis_test_runs_definition", "hypothesis_definition_id", "created_at"),
        Index("ix_beta_hypothesis_test_runs_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    hypothesis_definition_id: Mapped[str] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    dataset_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_dataset_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    model_version_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_model_versions.id", ondelete="SET NULL"),
        nullable=True,
    )
    validation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_validation_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    baseline_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    test_start_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    test_end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sample_size: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    matched_instruments: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    average_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    median_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    win_rate_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    max_drawdown_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_sign_accuracy_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    transaction_cost_bps: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    transaction_cost_adjusted_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    walk_forward_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    out_of_sample_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    regime_slice_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaHypothesisBeliefState(BetaBase):
    __tablename__ = "beta_hypothesis_belief_states"
    __table_args__ = (
        CheckConstraint(
            "status IN ('CANDIDATE','PROMISING','VALIDATED','DEGRADED','REJECTED','ARCHIVED')",
            name="ck_beta_hypothesis_belief_states_status",
        ),
        Index("ix_beta_hypothesis_belief_states_status", "status", "confidence_score"),
        Index("ix_beta_hypothesis_belief_states_updated", "updated_at"),
    )

    hypothesis_definition_id: Mapped[str] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id", ondelete="CASCADE"),
        primary_key=True,
    )
    confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    evidence_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    in_sample_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    out_of_sample_strength: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    degradation_rate: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    last_validated_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="CANDIDATE")
    supporting_test_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_test_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    contradicting_test_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_test_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaSignalObservation(BetaBase):
    __tablename__ = "beta_signal_observations"
    __table_args__ = (
        CheckConstraint(
            "expected_direction IN ('BULLISH','BEARISH','NEUTRAL','RISK_OFF')",
            name="ck_beta_signal_observations_direction",
        ),
        Index("ix_beta_signal_observations_definition", "hypothesis_definition_id", "observation_time"),
        Index("ix_beta_signal_observations_instrument", "instrument_id", "observation_time"),
        Index("ix_beta_signal_observations_status", "observation_status", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    hypothesis_definition_id: Mapped[str] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id", ondelete="CASCADE"),
        nullable=False,
    )
    hypothesis_test_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_test_runs.id", ondelete="SET NULL"),
        nullable=True,
    )
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    observation_time: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    decision_date: Mapped[date] = mapped_column(Date, nullable=False)
    matched_conditions_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    feature_snapshot_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    regime_context_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    prediction_source: Mapped[str | None] = mapped_column(String(40), nullable=True)
    expected_direction: Mapped[str] = mapped_column(String(20), nullable=False)
    expected_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    baseline_name: Mapped[str | None] = mapped_column(String(80), nullable=True)
    belief_confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    observation_status: Mapped[str] = mapped_column(String(30), nullable=False, default="MATCHED")
    realized_return_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    realized_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaRecommendationDecision(BetaBase):
    __tablename__ = "beta_recommendation_decisions"
    __table_args__ = (
        CheckConstraint(
            "decision_status IN ('WATCHING','RECOMMENDED','BLOCKED','DISMISSED','REJECTED')",
            name="ck_beta_recommendation_decisions_status",
        ),
        Index("ix_beta_recommendation_decisions_status", "decision_status", "created_at"),
        Index("ix_beta_recommendation_decisions_instrument", "instrument_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    signal_observation_id: Mapped[str] = mapped_column(
        ForeignKey("beta_signal_observations.id", ondelete="CASCADE"),
        nullable=False,
    )
    candidate_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    instrument_id: Mapped[str] = mapped_column(
        ForeignKey("beta_instruments.id", ondelete="CASCADE"),
        nullable=False,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    decision_status: Mapped[str] = mapped_column(String(20), nullable=False)
    decision_reason_code: Mapped[str | None] = mapped_column(String(80), nullable=True)
    decision_reason_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    belief_confidence_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    portfolio_constraint_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    paper_trade_action: Mapped[str | None] = mapped_column(String(40), nullable=True)
    recommendation_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaSignalCandidate(BetaBase):
    __tablename__ = "beta_signal_candidates"
    __table_args__ = (
        CheckConstraint(
            "status IN ('WATCHING','PROMOTED','DISMISSED','REJECTED')",
            name="ck_beta_signal_candidates_status",
        ),
        CheckConstraint(
            "direction IN ('BULLISH','BEARISH','NEUTRAL','RISK_OFF')",
            name="ck_beta_signal_candidates_direction",
        ),
        Index("ix_beta_signal_candidates_status", "status", "updated_at"),
        Index("ix_beta_signal_candidates_instrument", "instrument_id", "updated_at"),
        Index("ix_beta_signal_candidates_confidence", "confidence_score", "expected_edge_score"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    instrument_id: Mapped[str | None] = mapped_column(ForeignKey("beta_instruments.id", ondelete="SET NULL"), nullable=True)
    hypothesis_id: Mapped[str | None] = mapped_column(ForeignKey("beta_hypotheses.id", ondelete="SET NULL"), nullable=True)
    hypothesis_definition_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_hypothesis_definitions.id", ondelete="SET NULL"),
        nullable=True,
    )
    signal_observation_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_signal_observations.id", ondelete="SET NULL"),
        nullable=True,
    )
    recommendation_decision_id: Mapped[str | None] = mapped_column(
        ForeignKey("beta_recommendation_decisions.id", ondelete="SET NULL"),
        nullable=True,
    )
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="WATCHING")
    direction: Mapped[str] = mapped_column(String(20), nullable=False, default="BULLISH")
    confidence_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    expected_edge_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    market: Mapped[str | None] = mapped_column(String(20), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    discovered_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaSignalCandidateEvent(BetaBase):
    __tablename__ = "beta_signal_candidate_events"
    __table_args__ = (
        Index("ix_beta_signal_candidate_events_candidate", "candidate_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    candidate_id: Mapped[str] = mapped_column(
        ForeignKey("beta_signal_candidates.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaDemoPosition(BetaBase):
    __tablename__ = "beta_demo_positions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('OPEN','CLOSED','RISK_OFF_EXIT','CANCELLED')",
            name="ck_beta_demo_positions_status",
        ),
        CheckConstraint("side IN ('LONG','FLAT')", name="ck_beta_demo_positions_side"),
        Index("ix_beta_demo_positions_status", "status", "opened_at"),
        Index("ix_beta_demo_positions_symbol", "symbol", "opened_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    candidate_id: Mapped[str | None] = mapped_column(ForeignKey("beta_signal_candidates.id", ondelete="SET NULL"), nullable=True)
    symbol: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str | None] = mapped_column(String(20), nullable=True)
    side: Mapped[str] = mapped_column(String(20), nullable=False, default="LONG")
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="OPEN")
    confidence_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    expected_edge_score: Mapped[float] = mapped_column(nullable=False, default=0.0)
    size_gbp: Mapped[str] = mapped_column(String(30), nullable=False, default="0")
    units: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entry_price: Mapped[str | None] = mapped_column(String(30), nullable=True)
    entry_bar_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    exit_price: Mapped[str | None] = mapped_column(String(30), nullable=True)
    target_return_pct: Mapped[str | None] = mapped_column(String(30), nullable=True)
    stop_loss_pct: Mapped[str | None] = mapped_column(String(30), nullable=True)
    planned_horizon_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pnl_gbp: Mapped[str | None] = mapped_column(String(30), nullable=True)
    pnl_pct: Mapped[str | None] = mapped_column(String(30), nullable=True)
    exit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    opened_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow, onupdate=_utcnow)


class BetaDemoPositionEvent(BetaBase):
    __tablename__ = "beta_demo_position_events"
    __table_args__ = (
        Index("ix_beta_demo_position_events_position", "position_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    position_id: Mapped[str] = mapped_column(
        ForeignKey("beta_demo_positions.id", ondelete="CASCADE"),
        nullable=False,
    )
    event_type: Mapped[str] = mapped_column(String(40), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    payload_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaUiNotification(BetaBase):
    __tablename__ = "beta_ui_notifications"
    __table_args__ = (
        CheckConstraint(
            "severity IN ('INFO','SUCCESS','WARNING','ERROR')",
            name="ck_beta_ui_notifications_severity",
        ),
        Index("ix_beta_ui_notifications_created", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    notification_type: Mapped[str] = mapped_column(String(60), nullable=False)
    severity: Mapped[str] = mapped_column(String(20), nullable=False, default="INFO")
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    message_text: Mapped[str] = mapped_column(Text, nullable=False)
    target_table: Mapped[str | None] = mapped_column(String(80), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)


class BetaUiSummarySnapshot(BetaBase):
    __tablename__ = "beta_ui_summary_snapshots"
    __table_args__ = (
        Index("ix_beta_ui_summary_snapshots_date", "snapshot_date"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_new_uuid)
    snapshot_date: Mapped[date] = mapped_column(Date, nullable=False)
    summary_json: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, default=_utcnow)
