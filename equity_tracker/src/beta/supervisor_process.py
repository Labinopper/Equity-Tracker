"""Separate process that owns ongoing beta runtime housekeeping."""

from __future__ import annotations

import os
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .context import BetaContext
from .db.bootstrap import ensure_beta_schema
from .db.engine import BetaDatabaseEngine
from .services.evaluation_service import BetaEvaluationService
from .services.filing_service import BetaFilingService
from .services.feature_service import BetaFeatureService
from .services.label_service import BetaLabelService
from .services.news_service import BetaNewsService
from .services.observation_service import BetaObservationService
from .services.corpus_service import BetaCorpusService
from .services.reference_service import BetaReferenceService
from .services.replay_service import BetaReplayService
from .services.hypothesis_service import BetaHypothesisService
from .services.review_service import BetaReviewService
from .services.runtime_service import BetaRuntimeService
from .services.scoring_service import BetaScoringService
from .services.training_service import BetaTrainingService
from .settings import BetaSettings

_STOP_EVENT = threading.Event()


def _handle_stop(_signum, _frame) -> None:
    _STOP_EVENT.set()


def main() -> int:
    beta_db_path_raw = os.environ.get("EQUITY_BETA_DB_PATH", "").strip()
    core_db_path_raw = os.environ.get("EQUITY_BETA_CORE_DB_PATH", "").strip()
    if not beta_db_path_raw:
        return 1

    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)

    beta_db_path = Path(beta_db_path_raw)
    core_db_path = Path(core_db_path_raw) if core_db_path_raw else None

    engine = BetaDatabaseEngine.open(beta_db_path)
    BetaContext.initialize(engine)
    ensure_beta_schema(engine, beta_db_path=beta_db_path)

    settings = BetaSettings.load(beta_db_path)
    BetaRuntimeService.record_job_run(
        job_name="beta_supervisor_bootstrap",
        job_type="supervisor",
        status="SUCCESS",
        details={"beta_db_path": str(beta_db_path)},
    )
    BetaRuntimeService.record_notification(
        notification_type="runtime",
        severity="INFO",
        title="Beta supervisor online",
        message_text="Background beta runtime is running.",
    )

    try:
        next_reference_sync_at = datetime.now(timezone.utc)
        next_news_sync_at = datetime.now(timezone.utc)
        next_filing_sync_at = datetime.now(timezone.utc)
        next_observation_at = datetime.now(timezone.utc)
        next_scoring_at = datetime.now(timezone.utc)
        while not _STOP_EVENT.wait(15):
            settings = BetaSettings.load(beta_db_path)
            BetaRuntimeService.sync_system_status(
                core_db_path=core_db_path,
                beta_db_path=beta_db_path,
                settings=settings,
            )
            now = datetime.now(timezone.utc)
            if settings.observation_enabled and now >= next_reference_sync_at:
                reference_result = BetaReferenceService.sync_seed_universe()
                BetaRuntimeService.record_job_run(
                    job_name="beta_learning_universe_sync",
                    job_type="reference",
                    status="SUCCESS",
                    details=reference_result,
                )
                if reference_result.get("memberships_added") or reference_result.get("memberships_removed"):
                    BetaRuntimeService.record_notification(
                        notification_type="reference_sync",
                        severity="INFO",
                        title="Beta universe changed",
                        message_text=(
                            f"Added {reference_result.get('memberships_added', 0)} memberships, removed "
                            f"{reference_result.get('memberships_removed', 0)}, target "
                            f"{reference_result.get('target_total', 0)} names."
                        ),
                )
                next_reference_sync_at = now + timedelta(hours=6)
            if settings.news_enabled and now >= next_news_sync_at:
                news_result = BetaNewsService.ingest_active_sources()
                BetaRuntimeService.record_job_run(
                    job_name="beta_news_sync",
                    job_type="news",
                    status="SUCCESS",
                    details=news_result,
                )
                if news_result.get("articles_stored") or news_result.get("links_stored"):
                    BetaRuntimeService.record_notification(
                        notification_type="news",
                        severity="INFO",
                        title="Beta news sync completed",
                        message_text=(
                            f"Stored {news_result.get('articles_stored', 0)} articles and "
                            f"{news_result.get('links_stored', 0)} symbol links."
                        ),
                    )
                next_news_sync_at = now + timedelta(hours=1)
            if settings.filings_enabled and now >= next_filing_sync_at:
                filing_result = BetaFilingService.ingest_active_sources()
                BetaRuntimeService.record_job_run(
                    job_name="beta_filing_sync",
                    job_type="filings",
                    status="SUCCESS",
                    details=filing_result,
                )
                if filing_result.get("events_stored") or filing_result.get("links_stored"):
                    BetaRuntimeService.record_notification(
                        notification_type="filings",
                        severity="INFO",
                        title="Official release sync completed",
                        message_text=(
                            f"Stored {filing_result.get('events_stored', 0)} official events and "
                            f"{filing_result.get('links_stored', 0)} linked symbols."
                        ),
                    )
                next_filing_sync_at = now + timedelta(hours=2)
            if settings.observation_enabled and now >= next_observation_at:
                observation_result = BetaObservationService.sync_daily_bars()
                intraday_result = BetaObservationService.sync_intraday_snapshots()
                corpus_result = BetaCorpusService.backfill_market_corpus(include_benchmarks=True)
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
                next_observation_at = now + timedelta(minutes=1)

            if settings.shadow_scoring_enabled and now >= next_scoring_at:
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
                next_scoring_at = now + timedelta(minutes=max(1, settings.shadow_default_cadence_minutes))
            BetaRuntimeService.ensure_daily_snapshot(settings)
            replay_result = BetaReplayService.ensure_daily_dashboard_pack()
            if replay_result.get("created"):
                BetaRuntimeService.record_job_run(
                    job_name="beta_daily_replay_pack",
                    job_type="replay",
                    status="SUCCESS",
                    details=replay_result,
                )
    finally:
        BetaRuntimeService.record_job_run(
            job_name="beta_supervisor_shutdown",
            job_type="supervisor",
            status="SUCCESS",
        )
        BetaRuntimeService.record_notification(
            notification_type="runtime",
            severity="INFO",
            title="Beta supervisor stopped",
            message_text="Background beta runtime has shut down.",
        )
        BetaContext.lock()
        engine.dispose()

    time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
