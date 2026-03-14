"""Web-process bootstrap for the separate paper-trading beta runtime."""

from __future__ import annotations

import os
import subprocess
import sys
import time
from pathlib import Path

from ..process_lock import active_process_lock_pid
from .context import BetaContext
from .db.bootstrap import apply_beta_schema_migrations, ensure_beta_schema
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
from .services.hypothesis_definition_service import BetaHypothesisDefinitionService
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


def _supervisor_lock_path() -> Path:
    return _equity_tracker_root().parent / "data" / "beta_supervisor.lock"


def _supervisor_env(core_db_path: Path, beta_db_path: Path) -> dict[str, str]:
    env = os.environ.copy()
    env["EQUITY_DB_PATH"] = str(core_db_path)
    env["EQUITY_BETA_CORE_DB_PATH"] = str(core_db_path)
    env["EQUITY_BETA_DB_PATH"] = str(beta_db_path)
    env["EQUITY_BETA_SUPERVISOR"] = "1"
    env["EQUITY_DB_ENCRYPTED"] = os.environ.get("EQUITY_DB_ENCRYPTED", "true")
    if "EQUITY_DB_PASSWORD" in os.environ:
        env["EQUITY_DB_PASSWORD"] = os.environ["EQUITY_DB_PASSWORD"]
    return env


def _bootstrap_beta_metadata(*, core_db_path: Path | None, beta_db_path: Path, settings: BetaSettings) -> None:
    """Perform only lightweight local bootstrap work in the web process."""
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
            "mode": settings.mode,
            "enabled": settings.enabled,
            "web_ui_enabled": settings.web_ui_enabled,
            "background_jobs_enabled": settings.background_jobs_enabled,
        },
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
    research_seed_result = BetaHypothesisDefinitionService.ensure_default_research_objects()
    BetaRuntimeService.record_job_run(
        job_name="beta_hypothesis_definition_seed",
        job_type="research_registry",
        status="SUCCESS",
        details=research_seed_result,
    )
    if research_seed_result.get("families_added") or research_seed_result.get("definitions_added"):
        BetaRuntimeService.record_notification(
            notification_type="hypothesis_registry",
            severity="INFO",
            title="Research definitions seeded",
            message_text=(
                f"Added {research_seed_result.get('families_added', 0)} families and "
                f"{research_seed_result.get('definitions_added', 0)} definitions."
            ),
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
    BetaRuntimeService.ensure_daily_snapshot(settings)
    replay_result = BetaReplayService.ensure_daily_dashboard_pack()
    if replay_result.get("created"):
        BetaRuntimeService.record_job_run(
            job_name="beta_daily_replay_pack",
            job_type="replay",
            status="SUCCESS",
            details=replay_result,
        )


def beta_ui_is_enabled(beta_db_path: Path | None) -> bool:
    if beta_db_path is None:
        return False
    settings = BetaSettings.load(beta_db_path)
    return bool(settings.enabled and settings.web_ui_enabled and settings.mode != "OFF")


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
                and settings.background_jobs_enabled
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
        if beta_db_preexisted:
            apply_beta_schema_migrations(engine)
        BetaContext.initialize(engine)
        ensure_beta_schema(engine, beta_db_path=beta_db_path)
        set_beta_db_path(beta_db_path)

        settings = BetaSettings.load(beta_db_path)
        if settings.pause_on_startup:
            settings.learning_enabled = False
            settings.shadow_scoring_enabled = False
            settings.demo_execution_enabled = False
            settings.save()

        _bootstrap_beta_metadata(core_db_path=core_db_path, beta_db_path=beta_db_path, settings=settings)

        if (
            allow_supervisor
            and core_db_path is not None
            and settings.enabled
            and settings.background_jobs_enabled
            and settings.auto_start_supervisor
            and settings.mode != "OFF"
        ):
            _start_supervisor(core_db_path, beta_db_path, settings)
    except Exception as exc:
        record_supervisor_error(str(exc))
        shutdown_beta_runtime()
        return None

    return beta_db_path


def reload_beta_runtime(core_db_path: Path | None) -> Path | None:
    shutdown_beta_runtime()
    return initialize_beta_runtime(core_db_path)


def shutdown_beta_runtime(*, stop_supervisor: bool = True) -> None:
    """Release the beta DB context and optionally stop the detached supervisor."""
    current_beta_db_path = get_beta_db_path()
    if stop_supervisor:
        _stop_supervisor_process()
        if current_beta_db_path is not None:
            settings = BetaSettings.load(current_beta_db_path)
            core_db_path_str = os.environ.get("EQUITY_DB_PATH", "").strip()
            BetaRuntimeService.sync_system_status(
                core_db_path=Path(core_db_path_str) if core_db_path_str else None,
                beta_db_path=current_beta_db_path,
                settings=settings,
                supervisor_status="stopped",
                supervisor_pid=None,
            )
    set_beta_db_path(None)
    BetaContext.lock()


def _start_supervisor(core_db_path: Path, beta_db_path: Path, settings: BetaSettings) -> None:
    global _SUPERVISOR_PROCESS

    if _SUPERVISOR_PROCESS is not None and _SUPERVISOR_PROCESS.poll() is None:
        return

    existing_pid = active_process_lock_pid(_supervisor_lock_path())
    if existing_pid is not None:
        record_supervisor_started(existing_pid)
        BetaRuntimeService.sync_system_status(
            core_db_path=core_db_path,
            beta_db_path=beta_db_path,
            settings=settings,
            supervisor_status="running",
            supervisor_pid=existing_pid,
        )
        return

    try:
        creationflags = 0
        if os.name == "nt":
            creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            creationflags |= getattr(subprocess, "DETACHED_PROCESS", 0)
            creationflags |= getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        process = subprocess.Popen(
            [sys.executable, "-m", "src.beta.supervisor_process"],
            cwd=str(_equity_tracker_root()),
            env=_supervisor_env(core_db_path, beta_db_path),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
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

    time.sleep(0.4)
    existing_pid = active_process_lock_pid(_supervisor_lock_path())
    if process.poll() is not None and existing_pid is None:
        message = f"Supervisor exited immediately with code {process.returncode}."
        record_supervisor_error(message)
        BetaRuntimeService.sync_system_status(
            core_db_path=core_db_path,
            beta_db_path=beta_db_path,
            settings=settings,
            last_error=message,
        )
        BetaRuntimeService.record_notification(
            notification_type="runtime",
            severity="ERROR",
            title="Beta supervisor failed to stay online",
            message_text=message,
        )
        return

    if existing_pid is not None:
        _SUPERVISOR_PROCESS = process if process.poll() is None else None
        record_supervisor_started(existing_pid)
        BetaRuntimeService.sync_system_status(
            core_db_path=core_db_path,
            beta_db_path=beta_db_path,
            settings=settings,
            supervisor_status="running",
            supervisor_pid=existing_pid,
        )
        if process.poll() is None and process.pid == existing_pid:
            BetaRuntimeService.record_notification(
                notification_type="runtime",
                severity="SUCCESS",
                title="Beta supervisor started",
                message_text=f"Supervisor running with PID {existing_pid}.",
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

    def _terminate_pid(pid: int | None) -> None:
        if pid is None:
            return
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass

    process = _SUPERVISOR_PROCESS
    target_pid = process.pid if process is not None else active_process_lock_pid(_supervisor_lock_path())
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
    if active_process_lock_pid(_supervisor_lock_path()) is not None:
        _terminate_pid(active_process_lock_pid(_supervisor_lock_path()) or target_pid)

    _SUPERVISOR_PROCESS = None
    record_supervisor_stopped()
