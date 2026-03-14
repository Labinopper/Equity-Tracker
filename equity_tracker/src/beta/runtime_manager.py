"""Web-process bootstrap for the separate paper-trading beta runtime."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from .context import BetaContext
from .db.bootstrap import apply_beta_schema_migrations, archive_incompatible_beta_db, ensure_beta_schema
from .db.engine import BetaDatabaseEngine
from .paths import resolve_beta_db_path
from .services.hypothesis_service import BetaHypothesisService
from .services.evaluation_service import BetaEvaluationService
from .services.filing_service import BetaFilingService
from .services.feature_service import BetaFeatureService
from .services.label_service import BetaLabelService
from .services.news_service import BetaNewsService
from .services.observation_service import BetaObservationService
from .services.corpus_service import BetaCorpusService
from .services.reference_service import BetaReferenceService
from .services.replay_service import BetaReplayService
from .services.review_service import BetaReviewService
from .services.runtime_service import BetaRuntimeService
from .services.scoring_service import BetaScoringService
from .services.training_service import BetaTrainingService
from .settings import BetaSettings
from .state import (
    get_beta_db_path,
    record_supervisor_error,
    record_supervisor_started,
    record_supervisor_stopped,
    set_beta_db_path,
)

_SUPERVISOR_PROCESS: subprocess.Popen | None = None


def _equity_tracker_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _supervisor_env(core_db_path: Path, beta_db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["EQUITY_BETA_CORE_DB_PATH"] = str(core_db_path)
    env["EQUITY_BETA_DB_PATH"] = str(beta_db_path)
    return env


def initialize_beta_runtime(core_db_path: Path | None, *, allow_supervisor: bool = True) -> Path | None:
    """Bootstrap the beta DB and, when enabled, start the supervisor process."""
    beta_db_path = resolve_beta_db_path(core_db_path)
    if beta_db_path is None:
        return None

    try:
        previous_path = get_beta_db_path()
        if previous_path is not None and previous_path == beta_db_path and BetaContext.is_initialized():
            settings = BetaSettings.load(beta_db_path)
            BetaRuntimeService.sync_system_status(
                core_db_path=core_db_path,
                beta_db_path=beta_db_path,
                settings=settings,
            )
            if (
                allow_supervisor
                and core_db_path is not None
                and settings.enabled
                and settings.auto_start_supervisor
                and settings.mode != "OFF"
            ):
                _start_supervisor(core_db_path, beta_db_path, settings)
            return beta_db_path

        if previous_path is not None and previous_path != beta_db_path:
            shutdown_beta_runtime()

        beta_db_path.parent.mkdir(parents=True, exist_ok=True)
        beta_db_preexisted = beta_db_path.exists()
        engine = BetaDatabaseEngine.open(beta_db_path)
        schema_migrations: list[str] = []
        archived_path = None
        migration_error: str | None = None
        if beta_db_preexisted:
            try:
                schema_migrations = apply_beta_schema_migrations(engine)
            except Exception as exc:
                migration_error = str(exc)
                engine.dispose()
                archived_path = archive_incompatible_beta_db(beta_db_path)
                engine = BetaDatabaseEngine.open(beta_db_path)
        BetaContext.initialize(engine)
        ensure_beta_schema(engine, beta_db_path=beta_db_path)
        set_beta_db_path(beta_db_path)

        settings = BetaSettings.load(beta_db_path)
        if settings.pause_on_startup:
            settings.learning_enabled = False
            settings.shadow_scoring_enabled = False
            settings.demo_execution_enabled = False
            settings.save()

        BetaRuntimeService.sync_system_status(
            core_db_path=core_db_path,
            beta_db_path=beta_db_path,
            settings=settings,
        )
        BetaRuntimeService.record_job_run(
            job_name="beta_runtime_bootstrap",
            job_type="runtime",
            status="SUCCESS",
            details={
                "allow_supervisor": allow_supervisor,
                "mode": settings.mode,
                "schema_migrations": schema_migrations,
                "migration_error": migration_error,
                "archived_path": str(archived_path) if archived_path is not None else None,
            },
        )
        if schema_migrations:
            BetaRuntimeService.record_notification(
                notification_type="runtime",
                severity="INFO",
                title="Beta research DB migrated in place",
                message_text=f"Applied {len(schema_migrations)} additive schema updates to the beta research DB.",
            )
        if archived_path is not None:
            BetaRuntimeService.record_notification(
                notification_type="runtime",
                severity="WARNING",
                title="Beta research DB was refreshed",
                message_text=(
                    "An older incompatible beta research DB was archived to "
                    f"{archived_path.name} and a fresh schema was created."
                ),
            )
        if core_db_path is not None:
            seed_result = BetaReferenceService.sync_seed_universe()
            BetaRuntimeService.record_job_run(
                job_name="beta_seed_universe_sync",
                job_type="reference",
                status="SUCCESS",
                details=seed_result,
            )
            if (
                seed_result.get("instruments_added")
                or seed_result.get("memberships_added")
                or seed_result.get("memberships_removed")
            ):
                BetaRuntimeService.record_notification(
                    notification_type="reference_sync",
                    severity="INFO",
                    title="Beta learning universe refreshed",
                    message_text=(
                        "Added "
                        f"{seed_result.get('instruments_added', 0)} instruments and "
                        f"{seed_result.get('memberships_added', 0)} memberships, removed "
                        f"{seed_result.get('memberships_removed', 0)} memberships, "
                        f"targeting {seed_result.get('target_total', 0)} names."
                    ),
                )
        hypothesis_result = BetaHypothesisService.ensure_default_hypotheses()
        BetaRuntimeService.record_job_run(
            job_name="beta_hypothesis_seed",
            job_type="research_registry",
            status="SUCCESS",
            details=hypothesis_result,
        )
        if hypothesis_result.get("added"):
            BetaRuntimeService.record_notification(
                notification_type="hypothesis_registry",
                severity="INFO",
                title="Default hypothesis families seeded",
                message_text=f"Added {hypothesis_result.get('added', 0)} research families.",
            )
        if settings.news_enabled:
            news_source_result = BetaNewsService.ensure_default_sources()
            BetaRuntimeService.record_job_run(
                job_name="beta_news_source_seed",
                job_type="news",
                status="SUCCESS",
                details=news_source_result,
            )
        if settings.filings_enabled:
            filing_source_result = BetaFilingService.ensure_default_sources()
            BetaRuntimeService.record_job_run(
                job_name="beta_filing_source_seed",
                job_type="filings",
                status="SUCCESS",
                details=filing_source_result,
            )
        if allow_supervisor and settings.news_enabled:
            news_result = BetaNewsService.ingest_active_sources()
            BetaRuntimeService.record_job_run(
                job_name="beta_news_sync",
                job_type="news",
                status="SUCCESS",
                details=news_result,
            )
        if allow_supervisor and settings.filings_enabled:
            filing_result = BetaFilingService.ingest_active_sources()
            BetaRuntimeService.record_job_run(
                job_name="beta_filing_sync",
                job_type="filings",
                status="SUCCESS",
                details=filing_result,
            )
        if settings.observation_enabled:
            observation_result = BetaObservationService.sync_daily_bars()
            intraday_result = BetaObservationService.sync_intraday_snapshots()
            corpus_result = (
                BetaCorpusService.backfill_market_corpus(include_benchmarks=True)
                if allow_supervisor
                else {
                    "catalog_updates": 0,
                    "benchmarks_added": 0,
                    "instrument_bars_added": 0,
                    "instruments_backfilled": 0,
                }
            )
            BetaRuntimeService.record_job_run(
                job_name="beta_daily_observation_sync",
                job_type="observation",
                status="SUCCESS",
                details={
                    "daily": observation_result,
                    "intraday": intraday_result,
                    "corpus": corpus_result,
                },
            )
            if corpus_result.get("instrument_bars_added") or corpus_result.get("benchmarks_added"):
                BetaRuntimeService.record_notification(
                    notification_type="corpus",
                    severity="INFO",
                    title="Beta corpus backfill progressed",
                    message_text=(
                        f"Added {corpus_result.get('instrument_bars_added', 0)} daily bars across "
                        f"{corpus_result.get('instruments_backfilled', 0)} instruments and "
                        f"{corpus_result.get('benchmarks_added', 0)} benchmark bars."
                    ),
                )
            feature_result = BetaFeatureService.generate_daily_features()
            BetaRuntimeService.record_job_run(
                job_name="beta_daily_feature_build",
                job_type="feature_store",
                status="SUCCESS",
                details=feature_result,
            )
            label_result = BetaLabelService.generate_daily_labels()
            BetaRuntimeService.record_job_run(
                job_name="beta_daily_label_build",
                job_type="label_store",
                status="SUCCESS",
                details=label_result,
            )
        if allow_supervisor and settings.filings_enabled and (filing_result.get("events_stored") or filing_result.get("links_stored")):
            BetaRuntimeService.record_notification(
                notification_type="filings",
                severity="INFO",
                title="Official release sync completed",
                message_text=(
                    f"Stored {filing_result.get('events_stored', 0)} official events and "
                    f"{filing_result.get('links_stored', 0)} linked symbols."
                ),
            )
        if settings.shadow_scoring_enabled:
            scoring_result = BetaScoringService.run_daily_shadow_cycle(settings)
            BetaRuntimeService.record_job_run(
                job_name="beta_daily_shadow_cycle",
                job_type="scoring",
                status="SUCCESS",
                details=scoring_result,
            )
            if scoring_result.get("recommended") or scoring_result.get("positions_opened") or scoring_result.get("positions_closed"):
                BetaRuntimeService.record_notification(
                    notification_type="shadow_cycle",
                    severity="INFO",
                    title="Beta scoring cycle completed",
                    message_text=(
                        f"Recommended {scoring_result.get('recommended', 0)} signals, "
                        f"opened {scoring_result.get('positions_opened', 0)} demo trades, "
                        f"closed {scoring_result.get('positions_closed', 0)}."
                    ),
                )
            if scoring_result.get("entries_paused_changed"):
                paused = bool(scoring_result.get("entries_paused"))
                BetaRuntimeService.record_notification(
                    notification_type="risk_control",
                    severity="WARNING" if paused else "SUCCESS",
                    title="Demo entry gate changed",
                    message_text=(
                        "New demo entries were paused by degradation control."
                        if paused
                        else "New demo entries were resumed after recovery."
                    ),
                )
            evaluation_result = BetaEvaluationService.run_live_evaluation()
            BetaRuntimeService.record_job_run(
                job_name="beta_live_evaluation",
                job_type="evaluation",
                status="SUCCESS",
                details=evaluation_result,
            )
            hypothesis_refresh = BetaHypothesisService.refresh_hypotheses()
            BetaRuntimeService.record_job_run(
                job_name="beta_hypothesis_refresh",
                job_type="research_registry",
                status="SUCCESS",
                details=hypothesis_refresh,
            )
            if hypothesis_refresh.get("changed"):
                BetaRuntimeService.record_notification(
                    notification_type="hypothesis_registry",
                    severity="INFO",
                    title="Hypothesis registry changed",
                    message_text=(
                        f"Changed {hypothesis_refresh.get('changed', 0)} hypothesis states; "
                        f"promoted {hypothesis_refresh.get('promoted', 0)}, suspended "
                        f"{hypothesis_refresh.get('suspended', 0)}."
                    ),
                )
                for change in hypothesis_refresh.get("changes_detail", [])[:6]:
                    BetaRuntimeService.record_notification(
                        notification_type="hypothesis_registry",
                        severity="INFO",
                        title=str(change.get("title") or "Hypothesis updated"),
                        message_text=(
                            f"{change.get('status_before')} -> {change.get('status_after')} "
                            f"at evidence {change.get('evidence_score')}."
                        ),
                        target_table="beta_hypotheses",
                        target_id=str(change.get("hypothesis_id") or ""),
                    )
            if settings.training_enabled:
                training_result = BetaTrainingService.ensure_daily_training()
                if training_result.get("performed"):
                    BetaRuntimeService.record_job_run(
                        job_name="beta_daily_training",
                        job_type="training",
                        status="SUCCESS" if training_result.get("trained") else "SKIPPED",
                        details=training_result,
                    )
                    if training_result.get("trained"):
                        BetaRuntimeService.record_notification(
                            notification_type="training",
                            severity="SUCCESS",
                            title="Beta model training completed",
                            message_text=(
                                f"Stored model {training_result.get('version_code')} with validation sign accuracy "
                                f"{training_result.get('validation_sign_accuracy_pct', 0)}%."
                            ),
                        )
            review_result = BetaReviewService.ensure_daily_potential_gains_review()
            if review_result.get("performed"):
                BetaRuntimeService.record_job_run(
                    job_name="beta_daily_potential_gains_review",
                    job_type="review",
                    status="SUCCESS",
                    details=review_result,
                )
                BetaRuntimeService.record_notification(
                    notification_type="review",
                    severity="INFO",
                    title="Daily beta review stored",
                    message_text=(
                        "Stored a daily potential-gains review with "
                        f"{review_result.get('findings', 0)} findings."
                    ),
                )
        BetaRuntimeService.ensure_daily_snapshot(settings)
        replay_result = BetaReplayService.ensure_daily_dashboard_pack()
        if replay_result.get("created"):
            BetaRuntimeService.record_job_run(
                job_name="beta_daily_replay_pack",
                job_type="replay",
                status="SUCCESS",
                details=replay_result,
            )

        if (
            allow_supervisor
            and core_db_path is not None
            and settings.enabled
            and settings.auto_start_supervisor
            and settings.mode != "OFF"
        ):
            _start_supervisor(core_db_path, beta_db_path, settings)
    except Exception:
        shutdown_beta_runtime()
        return None

    return beta_db_path


def reload_beta_runtime(core_db_path: Path | None) -> Path | None:
    shutdown_beta_runtime()
    return initialize_beta_runtime(core_db_path)


def shutdown_beta_runtime() -> None:
    """Stop the supervisor and release the beta DB context."""
    _stop_supervisor_process()
    set_beta_db_path(None)
    BetaContext.lock()


def _start_supervisor(core_db_path: Path, beta_db_path: Path, settings: BetaSettings) -> None:
    global _SUPERVISOR_PROCESS

    if _SUPERVISOR_PROCESS is not None and _SUPERVISOR_PROCESS.poll() is None:
        return

    try:
        process = subprocess.Popen(
            [sys.executable, "-m", "src.beta.supervisor_process"],
            cwd=str(_equity_tracker_root()),
            env=_supervisor_env(core_db_path, beta_db_path),
        )
    except Exception as exc:
        record_supervisor_error(str(exc))
        BetaRuntimeService.sync_system_status(
            core_db_path=core_db_path,
            beta_db_path=beta_db_path,
            settings=settings,
            last_error=str(exc),
        )
        BetaRuntimeService.record_notification(
            notification_type="runtime",
            severity="ERROR",
            title="Beta supervisor failed to start",
            message_text=str(exc),
        )
        return

    _SUPERVISOR_PROCESS = process
    record_supervisor_started(process.pid)
    BetaRuntimeService.sync_system_status(
        core_db_path=core_db_path,
        beta_db_path=beta_db_path,
        settings=settings,
    )
    BetaRuntimeService.record_notification(
        notification_type="runtime",
        severity="SUCCESS",
        title="Beta supervisor started",
        message_text=f"Supervisor running with PID {process.pid}.",
    )


def _stop_supervisor_process() -> None:
    global _SUPERVISOR_PROCESS

    process = _SUPERVISOR_PROCESS
    if process is None:
        record_supervisor_stopped()
        return

    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    _SUPERVISOR_PROCESS = None
    record_supervisor_stopped()
