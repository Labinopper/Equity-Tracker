"""Separate process that owns ongoing beta runtime housekeeping."""

from __future__ import annotations

import gc
import os
import signal
import threading
import time
import ctypes
from datetime import datetime, timedelta, timezone
from pathlib import Path
from ctypes import wintypes

from ..process_lock import acquire_process_lock
from .context import BetaContext
from .db.bootstrap import ensure_beta_schema
from .db.engine import BetaDatabaseEngine
from .services.evaluation_service import BetaEvaluationService
from .services.filing_service import BetaFilingService
from .services.feature_service import BetaFeatureService
from .services.hypothesis_backtest_service import BetaHypothesisBacktestService
from .services.hypothesis_belief_service import BetaHypothesisBeliefService
from .services.hypothesis_definition_service import BetaHypothesisDefinitionService
from .services.label_service import BetaLabelService
from .services.news_service import BetaNewsService
from .services.observation_service import BetaObservationService
from .services.corpus_service import BetaCorpusService
from .services.pipeline_assessment_service import BetaPipelineAssessmentService
from .services.reference_service import BetaReferenceService
from .services.replay_service import BetaReplayService
from .services.hypothesis_service import BetaHypothesisService
from .services.review_service import BetaReviewService
from .services.runtime_service import BetaRuntimeService
from .services.scoring_service import BetaScoringService
from .services.training_service import BetaTrainingService
from .settings import BetaSettings

_STOP_EVENT = threading.Event()
_CORPUS_BACKFILL_BATCH_SIZE = 15
_FEATURE_BACKLOG_BATCH_SIZE = 4
_LABEL_BACKLOG_BATCH_SIZE = 4
_LAST_MEMORY_GUARD_AT: datetime | None = None
_MEMORY_GUARD_NOTIFICATION_INTERVAL = timedelta(minutes=5)


def _supervisor_lock_path() -> Path:
    return Path(__file__).resolve().parents[3] / "data" / "beta_supervisor.lock"


def _handle_stop(_signum, _frame) -> None:
    _STOP_EVENT.set()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _system_memory_used_pct() -> float | None:
    if os.name != "nt":
        return None

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", wintypes.DWORD),
            ("dwMemoryLoad", wintypes.DWORD),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    state = MEMORYSTATUSEX()
    state.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(state)):
        return None
    return float(state.dwMemoryLoad)


def _process_rss_mb() -> float | None:
    if os.name != "nt":
        return None

    class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(PROCESS_MEMORY_COUNTERS_EX)
    process_handle = ctypes.windll.kernel32.GetCurrentProcess()
    if not ctypes.windll.psapi.GetProcessMemoryInfo(process_handle, ctypes.byref(counters), counters.cb):
        return None
    return round(float(counters.WorkingSetSize) / (1024.0 * 1024.0), 2)


def _memory_guard_snapshot(settings: BetaSettings) -> dict[str, object]:
    system_used_pct = _system_memory_used_pct()
    process_rss_mb = _process_rss_mb()
    system_limit_pct = int(getattr(settings, "max_memory_pct", 75))
    process_limit_mb = int(getattr(settings, "max_memory_mb", 1024))

    reasons: list[str] = []
    if system_used_pct is not None and system_used_pct >= system_limit_pct:
        reasons.append("system_memory_above_limit")
    if process_rss_mb is not None:
        if system_used_pct is None and process_rss_mb >= process_limit_mb:
            reasons.append("process_rss_above_limit")
        elif system_used_pct is not None and process_rss_mb >= process_limit_mb and system_used_pct >= max(50, system_limit_pct - 5):
            reasons.append("process_rss_above_limit_under_pressure")

    return {
        "triggered": bool(reasons),
        "reasons": reasons,
        "system_memory_used_pct": round(system_used_pct, 2) if system_used_pct is not None else None,
        "process_rss_mb": process_rss_mb,
        "system_limit_pct": system_limit_pct,
        "process_limit_mb": process_limit_mb,
    }


def _maybe_pause_for_memory(*, settings: BetaSettings, phase: str) -> bool:
    global _LAST_MEMORY_GUARD_AT

    snapshot = _memory_guard_snapshot(settings)
    if not bool(snapshot.get("triggered")):
        return False

    gc.collect()
    now = _utcnow()
    if _LAST_MEMORY_GUARD_AT is None or (now - _LAST_MEMORY_GUARD_AT) >= _MEMORY_GUARD_NOTIFICATION_INTERVAL:
        details = dict(snapshot)
        details["phase"] = phase
        BetaRuntimeService.record_job_run(
            job_name="beta_memory_guard",
            job_type="resource_guard",
            status="SKIPPED",
            details=details,
        )
        BetaRuntimeService.record_notification(
            notification_type="resource_guard",
            severity="WARNING",
            title="Beta memory guard active",
            message_text=(
                f"Skipped heavy beta work during {phase}. "
                f"System RAM used {details.get('system_memory_used_pct')}% "
                f"(limit {details.get('system_limit_pct')}%), "
                f"process RSS {details.get('process_rss_mb')} MB "
                f"(limit {details.get('process_limit_mb')} MB)."
            ),
        )
        _LAST_MEMORY_GUARD_AT = now
    return True


def _record_job_failure(*, job_name: str, job_type: str, exc: Exception) -> None:
    message = str(exc) or exc.__class__.__name__
    BetaRuntimeService.record_job_run(
        job_name=job_name,
        job_type=job_type,
        status="FAILED",
        details={"error": message, "error_type": exc.__class__.__name__},
    )
    BetaRuntimeService.record_notification(
        notification_type=job_type,
        severity="ERROR",
        title=f"{job_name} failed",
        message_text=message,
    )


def _run_job(*, job_name: str, job_type: str, op):
    try:
        result = op()
    except Exception as exc:
        _record_job_failure(job_name=job_name, job_type=job_type, exc=exc)
        return None
    BetaRuntimeService.record_job_run(
        job_name=job_name,
        job_type=job_type,
        status="SUCCESS",
        details=result if isinstance(result, dict) else {"result": result},
    )
    return result


def _run_supervisor_cycle(
    *,
    core_db_path: Path | None,
    beta_db_path: Path,
    settings: BetaSettings,
    now: datetime,
    next_reference_sync_at: datetime,
    next_news_sync_at: datetime,
    next_filing_sync_at: datetime,
    next_observation_at: datetime,
    next_hypothesis_research_at: datetime,
    next_core_scoring_at: datetime,
    next_scoring_at: datetime,
) -> dict[str, datetime]:
    if not settings.enabled or settings.mode == "OFF" or not settings.background_jobs_enabled:
        return {
            "next_reference_sync_at": next_reference_sync_at,
            "next_news_sync_at": next_news_sync_at,
            "next_filing_sync_at": next_filing_sync_at,
            "next_observation_at": next_observation_at,
            "next_hypothesis_research_at": next_hypothesis_research_at,
            "next_core_scoring_at": next_core_scoring_at,
            "next_scoring_at": next_scoring_at,
        }

    if _maybe_pause_for_memory(settings=settings, phase="pre_cycle"):
        BetaRuntimeService.ensure_daily_snapshot(settings)
        BetaPipelineAssessmentService.record_snapshot(
            snapshot_type="SUPERVISOR_CYCLE",
            trigger_job_name="beta_memory_guard",
        )
        return {
            "next_reference_sync_at": next_reference_sync_at,
            "next_news_sync_at": next_news_sync_at,
            "next_filing_sync_at": next_filing_sync_at,
            "next_observation_at": next_observation_at,
            "next_hypothesis_research_at": next_hypothesis_research_at,
            "next_core_scoring_at": next_core_scoring_at,
            "next_scoring_at": next_scoring_at,
        }

    if settings.observation_enabled and now >= next_reference_sync_at:
        reference_result = _run_job(
            job_name="beta_learning_universe_sync",
            job_type="reference",
            op=BetaReferenceService.sync_seed_universe,
        )
        if reference_result is not None and (
            reference_result.get("memberships_added") or reference_result.get("memberships_removed")
        ):
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
        news_result = _run_job(
            job_name="beta_news_sync",
            job_type="news",
            op=BetaNewsService.ingest_active_sources,
        )
        if news_result is not None and (news_result.get("articles_stored") or news_result.get("links_stored")):
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
        filing_result = _run_job(
            job_name="beta_filing_sync",
            job_type="filings",
            op=BetaFilingService.ingest_active_sources,
        )
        if filing_result is not None and (filing_result.get("events_stored") or filing_result.get("links_stored")):
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
        observation_result = _run_job(
            job_name="beta_daily_observation_sync",
            job_type="observation",
            op=lambda: {
                "daily": BetaObservationService.sync_daily_bars(),
                "intraday": BetaObservationService.sync_intraday_snapshots(),
                "corpus": BetaCorpusService.backfill_market_corpus(
                    batch_size=_CORPUS_BACKFILL_BATCH_SIZE,
                    include_benchmarks=True,
                ),
            },
        )
        if observation_result is not None:
            corpus_result = observation_result.get("corpus", {})
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
        _run_job(
            job_name="beta_tracked_core_feature_build",
            job_type="feature_store",
            op=BetaFeatureService.generate_core_tracked_features,
        )
        _run_job(
            job_name="beta_tracked_core_label_build",
            job_type="label_store",
            op=BetaLabelService.generate_core_tracked_labels,
        )
        _run_job(
            job_name="beta_feature_backlog_build",
            job_type="feature_store",
            op=lambda: BetaFeatureService.generate_feature_backlog(batch_size=_FEATURE_BACKLOG_BATCH_SIZE),
        )
        _run_job(
            job_name="beta_label_backlog_build",
            job_type="label_store",
            op=lambda: BetaLabelService.generate_label_backlog(batch_size=_LABEL_BACKLOG_BATCH_SIZE),
        )
        _run_job(
            job_name="beta_research_universe_refresh",
            job_type="reference",
            op=lambda: BetaReferenceService.refresh_research_membership_states(
                refill_if_needed=bool(
                    observation_result
                    and observation_result.get("corpus", {}).get("instruments_retired", 0)
                )
            ),
        )
        next_observation_at = now + timedelta(minutes=1)

    if _maybe_pause_for_memory(settings=settings, phase="post_observation"):
        BetaRuntimeService.ensure_daily_snapshot(settings)
        BetaPipelineAssessmentService.record_snapshot(
            snapshot_type="SUPERVISOR_CYCLE",
            trigger_job_name="beta_memory_guard",
        )
        return {
            "next_reference_sync_at": next_reference_sync_at,
            "next_news_sync_at": next_news_sync_at,
            "next_filing_sync_at": next_filing_sync_at,
            "next_observation_at": next_observation_at,
            "next_hypothesis_research_at": next_hypothesis_research_at,
            "next_core_scoring_at": next_core_scoring_at,
            "next_scoring_at": next_scoring_at,
        }

    if settings.learning_enabled and now >= next_hypothesis_research_at:
        _run_job(
            job_name="beta_hypothesis_definition_seed",
            job_type="research_registry",
            op=BetaHypothesisDefinitionService.ensure_default_research_objects,
        )
        backtest_result = _run_job(
            job_name="beta_hypothesis_backtests",
            job_type="research_registry",
            op=BetaHypothesisBacktestService.refresh_backtests,
        )
        belief_result = _run_job(
            job_name="beta_hypothesis_belief_refresh",
            job_type="research_registry",
            op=BetaHypothesisBeliefService.refresh_belief_states,
        )
        hypothesis_refresh = _run_job(
            job_name="beta_hypothesis_refresh",
            job_type="research_registry",
            op=BetaHypothesisService.refresh_hypotheses,
        )
        if hypothesis_refresh is not None and hypothesis_refresh.get("changed"):
            BetaRuntimeService.record_notification(
                notification_type="hypothesis_registry",
                severity="INFO",
                title="Hypothesis registry changed",
                message_text=(
                    f"Changed {hypothesis_refresh.get('changed', 0)} family states; "
                    f"promoted {hypothesis_refresh.get('promoted', 0)}, suspended "
                    f"{hypothesis_refresh.get('suspended', 0)}."
                ),
            )
        if belief_result is not None and (
            belief_result.get("validated_definitions") or belief_result.get("promising_definitions")
        ):
            BetaRuntimeService.record_notification(
                notification_type="hypothesis_registry",
                severity="INFO",
                title="Hypothesis beliefs refreshed",
                message_text=(
                    f"Validated {belief_result.get('validated_definitions', 0)} definitions; "
                    f"promising {belief_result.get('promising_definitions', 0)}. "
                    f"Backtests written {backtest_result.get('test_runs_written', 0) if backtest_result is not None else 0}."
                ),
            )
        next_hypothesis_research_at = now + timedelta(hours=1)

    if _maybe_pause_for_memory(settings=settings, phase="post_research"):
        BetaRuntimeService.ensure_daily_snapshot(settings)
        BetaPipelineAssessmentService.record_snapshot(
            snapshot_type="SUPERVISOR_CYCLE",
            trigger_job_name="beta_memory_guard",
        )
        return {
            "next_reference_sync_at": next_reference_sync_at,
            "next_news_sync_at": next_news_sync_at,
            "next_filing_sync_at": next_filing_sync_at,
            "next_observation_at": next_observation_at,
            "next_hypothesis_research_at": next_hypothesis_research_at,
            "next_core_scoring_at": next_core_scoring_at,
            "next_scoring_at": next_scoring_at,
        }

    if settings.shadow_scoring_enabled and now >= next_core_scoring_at:
        _run_job(
            job_name="beta_tracked_core_shadow_cycle",
            job_type="scoring",
            op=lambda: BetaScoringService.run_daily_shadow_cycle(settings, core_only=True),
        )
        next_core_scoring_at = now + timedelta(minutes=1)

    if settings.shadow_scoring_enabled and now >= next_scoring_at:
        scoring_result = _run_job(
            job_name="beta_daily_shadow_cycle",
            job_type="scoring",
            op=lambda: BetaScoringService.run_daily_shadow_cycle(settings),
        )
        if scoring_result is not None:
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

        _run_job(
            job_name="beta_live_evaluation",
            job_type="evaluation",
            op=BetaEvaluationService.run_live_evaluation,
        )
        if settings.training_enabled:
            training_result = _run_job(
                job_name="beta_daily_training",
                job_type="training",
                op=BetaTrainingService.ensure_daily_training,
            )
            if training_result is not None and training_result.get("performed") and training_result.get("trained"):
                BetaRuntimeService.record_notification(
                    notification_type="training",
                    severity="SUCCESS",
                    title="Beta model training completed",
                    message_text=(
                        f"Stored model {training_result.get('version_code')} with validation sign accuracy "
                        f"{training_result.get('validation_sign_accuracy_pct', 0)}%."
                    ),
                )
        review_result = _run_job(
            job_name="beta_daily_potential_gains_review",
            job_type="review",
            op=BetaReviewService.ensure_daily_potential_gains_review,
        )
        if review_result is not None and review_result.get("performed"):
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

    _run_job(
        job_name="beta_daily_replay_pack",
        job_type="replay",
        op=BetaReplayService.ensure_daily_dashboard_pack,
    )
    BetaRuntimeService.ensure_daily_snapshot(settings)
    BetaPipelineAssessmentService.record_snapshot(
        snapshot_type="SUPERVISOR_CYCLE",
        trigger_job_name="beta_supervisor_cycle",
    )

    return {
        "next_reference_sync_at": next_reference_sync_at,
        "next_news_sync_at": next_news_sync_at,
        "next_filing_sync_at": next_filing_sync_at,
        "next_observation_at": next_observation_at,
        "next_hypothesis_research_at": next_hypothesis_research_at,
        "next_core_scoring_at": next_core_scoring_at,
        "next_scoring_at": next_scoring_at,
    }


def main() -> int:
    beta_db_path_raw = os.environ.get("EQUITY_BETA_DB_PATH", "").strip()
    core_db_path_raw = os.environ.get("EQUITY_BETA_CORE_DB_PATH", "").strip()
    if not beta_db_path_raw:
        return 1

    process_lock = acquire_process_lock(_supervisor_lock_path())
    if process_lock is None:
        return 0

    try:
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

        next_reference_sync_at = datetime.now(timezone.utc)
        next_news_sync_at = datetime.now(timezone.utc)
        next_filing_sync_at = datetime.now(timezone.utc)
        next_observation_at = datetime.now(timezone.utc)
        next_hypothesis_research_at = datetime.now(timezone.utc)
        next_core_scoring_at = datetime.now(timezone.utc)
        next_scoring_at = datetime.now(timezone.utc)
        while not _STOP_EVENT.wait(15):
            settings = BetaSettings.load(beta_db_path)
            BetaRuntimeService.sync_system_status(
                core_db_path=core_db_path,
                beta_db_path=beta_db_path,
                settings=settings,
                supervisor_status="running",
                supervisor_pid=os.getpid(),
            )
            now = datetime.now(timezone.utc)
            next_times = _run_supervisor_cycle(
                core_db_path=core_db_path,
                beta_db_path=beta_db_path,
                settings=settings,
                now=now,
                next_reference_sync_at=next_reference_sync_at,
                next_news_sync_at=next_news_sync_at,
                next_filing_sync_at=next_filing_sync_at,
                next_observation_at=next_observation_at,
                next_hypothesis_research_at=next_hypothesis_research_at,
                next_core_scoring_at=next_core_scoring_at,
                next_scoring_at=next_scoring_at,
            )
            next_reference_sync_at = next_times["next_reference_sync_at"]
            next_news_sync_at = next_times["next_news_sync_at"]
            next_filing_sync_at = next_times["next_filing_sync_at"]
            next_observation_at = next_times["next_observation_at"]
            next_hypothesis_research_at = next_times["next_hypothesis_research_at"]
            next_core_scoring_at = next_times["next_core_scoring_at"]
            next_scoring_at = next_times["next_scoring_at"]
    finally:
        try:
            if "settings" in locals() and "beta_db_path" in locals() and "core_db_path" in locals():
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
                BetaRuntimeService.sync_system_status(
                    core_db_path=core_db_path,
                    beta_db_path=beta_db_path,
                    settings=settings,
                    supervisor_status="stopped",
                    supervisor_pid=None,
                )
        finally:
            BetaContext.lock()
            if "engine" in locals():
                engine.dispose()
            process_lock.release()

    time.sleep(0.05)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
