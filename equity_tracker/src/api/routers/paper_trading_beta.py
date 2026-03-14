"""Server-rendered UI for the Paper Trading Beta."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from ...app_context import AppContext
from ...beta.services.evaluation_service import BetaEvaluationService
from ...beta.services.filing_service import BetaFilingService
from ...beta.services.feature_service import BetaFeatureService
from ...beta.services.hypothesis_service import BetaHypothesisService
from ...beta.services.label_service import BetaLabelService
from ...beta.services.news_service import BetaNewsService
from ...beta.services.corpus_service import BetaCorpusService
from ...beta.runtime_manager import beta_ui_is_enabled, initialize_beta_runtime, reload_beta_runtime
from ...beta.services.observation_service import BetaObservationService
from ...beta.services.overview_service import BetaOverviewService
from ...beta.services.reference_service import BetaReferenceService
from ...beta.services.replay_service import BetaReplayService
from ...beta.services.review_service import BetaReviewService
from ...beta.services.runtime_service import BetaRuntimeService
from ...beta.services.scoring_service import BetaScoringService
from ...beta.services.training_service import BetaTrainingService
from ...beta.settings import BetaSettings
from ...beta.state import get_beta_db_path
from .. import _state
from .._templates import templates
from ..dependencies import session_required

router = APIRouter(tags=["paper-trading-beta"], dependencies=[Depends(session_required)])

_HTML_UTF8_MEDIA_TYPE = "text/html; charset=utf-8"


def _locked_response(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "locked.html",
        {"request": request},
        status_code=503,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _html_template_response(
    request: Request,
    name: str,
    context: dict,
    *,
    status_code: int = 200,
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        name,
        context,
        status_code=status_code,
        media_type=_HTML_UTF8_MEDIA_TYPE,
    )


def _ensure_beta_ready() -> Path | None:
    if not AppContext.is_initialized():
        return None
    beta_db_path = get_beta_db_path()
    if beta_db_path is not None:
        return beta_db_path
    core_db_path = _state.get_db_path()
    if core_db_path is None:
        return None
    return initialize_beta_runtime(core_db_path, allow_supervisor=True)


def _load_beta_context() -> tuple[dict[str, object], BetaSettings | None]:
    beta_db_path = _ensure_beta_ready()
    if beta_db_path is None:
        return BetaOverviewService.get_dashboard(), None
    return BetaOverviewService.get_dashboard(), BetaSettings.load(beta_db_path)


def _beta_ui_available() -> bool:
    return beta_ui_is_enabled(get_beta_db_path() or _ensure_beta_ready())


def _beta_disabled_response() -> HTMLResponse:
    return HTMLResponse("Beta is unavailable.", status_code=404, media_type=_HTML_UTF8_MEDIA_TYPE)


def _badge_class(value: str | None) -> str:
    raw = str(value or "").upper()
    if raw in {"OPEN", "PROMOTED", "SUCCESS", "ACTIVE", "IMPROVING", "BULLISH"}:
        return "badge badge-insert"
    if raw in {"WARNING", "RISK_OFF_EXIT", "DISMISSED", "REJECTED", "RISK_OFF", "BEARISH", "DECLINING", "SUSPENDED"}:
        return "badge badge-warning"
    if raw in {"ERROR", "FAILED", "CANCELLED"}:
        return "badge badge-delete"
    return "badge badge-neutral"


@router.get("/paper-trading-beta", response_class=HTMLResponse, include_in_schema=False)
async def beta_overview(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    dashboard, settings = _load_beta_context()
    return _html_template_response(
        request,
        "paper_trading_beta/overview.html",
        {
            "request": request,
            "dashboard": dashboard,
            "beta_settings": settings,
            "badge_class": _badge_class,
        },
    )


@router.get("/paper-trading-beta/opportunities", response_class=HTMLResponse, include_in_schema=False)
async def beta_opportunities(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    dashboard, settings = _load_beta_context()
    return _html_template_response(
        request,
        "paper_trading_beta/opportunities.html",
        {
            "request": request,
            "dashboard": dashboard,
            "beta_settings": settings,
            "badge_class": _badge_class,
        },
    )


@router.get("/paper-trading-beta/hypotheses", response_class=HTMLResponse, include_in_schema=False)
async def beta_hypotheses(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    dashboard, settings = _load_beta_context()
    return _html_template_response(
        request,
        "paper_trading_beta/hypotheses.html",
        {
            "request": request,
            "dashboard": dashboard,
            "beta_settings": settings,
            "badge_class": _badge_class,
        },
    )


@router.get("/paper-trading-beta/trades", response_class=HTMLResponse, include_in_schema=False)
async def beta_trades(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    dashboard, settings = _load_beta_context()
    return _html_template_response(
        request,
        "paper_trading_beta/trades.html",
        {
            "request": request,
            "dashboard": dashboard,
            "beta_settings": settings,
            "badge_class": _badge_class,
        },
    )


@router.get("/paper-trading-beta/replay", response_class=HTMLResponse, include_in_schema=False)
async def beta_replay(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    dashboard, settings = _load_beta_context()
    return _html_template_response(
        request,
        "paper_trading_beta/replay.html",
        {
            "request": request,
            "dashboard": dashboard,
            "replay_packs": BetaReplayService.list_recent_packs(),
            "beta_settings": settings,
            "badge_class": _badge_class,
        },
    )


@router.get("/paper-trading-beta/hypothesis/{hypothesis_id}", response_class=HTMLResponse, include_in_schema=False)
async def beta_hypothesis_detail(request: Request, hypothesis_id: str) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    _ensure_beta_ready()
    detail = BetaOverviewService.get_hypothesis_detail(hypothesis_id)
    if detail is None:
        return _html_template_response(
            request,
            "paper_trading_beta/hypothesis_detail.html",
            {"request": request, "detail": None, "badge_class": _badge_class},
            status_code=404,
        )
    return _html_template_response(
        request,
        "paper_trading_beta/hypothesis_detail.html",
        {"request": request, "detail": detail, "badge_class": _badge_class},
    )


@router.get("/paper-trading-beta/candidate/{candidate_id}", response_class=HTMLResponse, include_in_schema=False)
async def beta_candidate_detail(request: Request, candidate_id: str) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    _ensure_beta_ready()
    detail = BetaOverviewService.get_candidate_detail(candidate_id)
    if detail is None:
        return _html_template_response(
            request,
            "paper_trading_beta/candidate_detail.html",
            {"request": request, "detail": None, "badge_class": _badge_class},
            status_code=404,
        )
    return _html_template_response(
        request,
        "paper_trading_beta/candidate_detail.html",
        {"request": request, "detail": detail, "badge_class": _badge_class},
    )


@router.get("/paper-trading-beta/trade/{position_id}", response_class=HTMLResponse, include_in_schema=False)
async def beta_trade_detail(request: Request, position_id: str) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    _ensure_beta_ready()
    detail = BetaOverviewService.get_trade_detail(position_id)
    if detail is None:
        return _html_template_response(
            request,
            "paper_trading_beta/trade_detail.html",
            {"request": request, "detail": None, "badge_class": _badge_class},
            status_code=404,
        )
    return _html_template_response(
        request,
        "paper_trading_beta/trade_detail.html",
        {"request": request, "detail": detail, "badge_class": _badge_class},
    )


@router.get("/paper-trading-beta/health", response_class=HTMLResponse, include_in_schema=False)
async def beta_health(request: Request) -> HTMLResponse:
    if not AppContext.is_initialized():
        return _locked_response(request)
    if not _beta_ui_available():
        return _beta_disabled_response()
    dashboard, settings = _load_beta_context()
    return _html_template_response(
        request,
        "paper_trading_beta/health.html",
        {
            "request": request,
            "dashboard": dashboard,
            "beta_settings": settings,
            "badge_class": _badge_class,
            "flash": request.query_params.get("msg"),
        },
    )


@router.post("/paper-trading-beta/control", include_in_schema=False)
async def beta_control(
    request: Request,
    action: str = Form(...),
) -> RedirectResponse:
    if not AppContext.is_initialized():
        return RedirectResponse("/paper-trading-beta/health?msg=Database+locked", status_code=303)
    if not _beta_ui_available():
        return RedirectResponse("/", status_code=303)

    beta_db_path = _ensure_beta_ready()
    core_db_path = _state.get_db_path()
    if beta_db_path is None:
        return RedirectResponse("/paper-trading-beta/health?msg=Beta+runtime+unavailable", status_code=303)

    settings = BetaSettings.load(beta_db_path)
    message = "No change applied."

    if action == "pause_learning":
        settings.learning_enabled = False
        settings.save()
        message = "Learning paused."
    elif action == "resume_learning":
        settings.learning_enabled = True
        settings.save()
        message = "Learning resumed."
    elif action == "pause_shadow":
        settings.shadow_scoring_enabled = False
        settings.save()
        message = "Shadow scoring paused."
    elif action == "resume_shadow":
        settings.shadow_scoring_enabled = True
        settings.save()
        message = "Shadow scoring resumed."
    elif action == "pause_demo":
        settings.demo_execution_enabled = False
        settings.save()
        message = "Demo trading paused."
    elif action == "resume_demo":
        settings.demo_execution_enabled = True
        settings.save()
        message = "Demo trading resumed."
    elif action == "pause_all":
        settings.learning_enabled = False
        settings.shadow_scoring_enabled = False
        settings.demo_execution_enabled = False
        settings.save()
        message = "Learning, shadow scoring, and demo trading paused."
    elif action == "resume_all":
        settings.learning_enabled = True
        settings.shadow_scoring_enabled = True
        settings.demo_execution_enabled = True
        settings.save()
        message = "Learning, shadow scoring, and demo trading resumed."
    elif action == "reload_runtime":
        reload_beta_runtime(core_db_path)
        beta_db_path = get_beta_db_path() or beta_db_path
        settings = BetaSettings.load(beta_db_path)
        message = "Beta runtime reloaded."
    elif action == "snapshot_now":
        BetaRuntimeService.ensure_daily_snapshot(settings)
        message = "Daily snapshot ensured."
    elif action == "build_replay_pack":
        replay_result = BetaReplayService.build_focus_replay_pack()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_replay_pack",
            job_type="replay",
            status="SUCCESS",
            details=replay_result,
        )
        message = (
            "Replay pack built with "
            f"{replay_result.get('candidate_count', 0)} candidates, "
            f"{replay_result.get('position_count', 0)} positions, and "
            f"{replay_result.get('hypothesis_count', 0)} hypotheses."
        )
    elif action == "run_cycle":
        reference_result = BetaReferenceService.sync_seed_universe()
        observation_result = BetaObservationService.sync_daily_bars()
        intraday_result = BetaObservationService.sync_intraday_snapshots()
        corpus_result = {
            "catalog_updates": 0,
            "benchmarks_added": 0,
            "instrument_bars_added": 0,
            "instruments_backfilled": 0,
        }
        feature_result = BetaFeatureService.generate_daily_features()
        label_result = BetaLabelService.generate_daily_labels()
        scoring_result = BetaScoringService.run_daily_shadow_cycle(settings)
        evaluation_result = BetaEvaluationService.run_live_evaluation()
        hypothesis_result = BetaHypothesisService.refresh_hypotheses()
        review_result = BetaReviewService.ensure_daily_potential_gains_review()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_cycle",
            job_type="manual_run",
            status="SUCCESS",
            details={
                "reference": reference_result,
                "observation": observation_result,
                "intraday": intraday_result,
                "corpus": corpus_result,
                "features": feature_result,
                "labels": label_result,
                "scoring": scoring_result,
                "evaluation": evaluation_result,
                "hypotheses": hypothesis_result,
                "review": review_result,
            },
        )
        message = (
            "Manual beta cycle completed: "
            f"{observation_result.get('bars_added', 0)} daily bars, "
            f"{intraday_result.get('snapshots_added', 0)} intraday snapshots, "
            f"{corpus_result.get('instrument_bars_added', 0)} corpus bars, "
            f"{scoring_result.get('recommended', 0)} recommendations, "
            f"trend {str(evaluation_result.get('trend_label', 'STABLE')).lower()}, "
            f"{hypothesis_result.get('changed', 0)} hypothesis state changes."
        )
    elif action == "run_hypothesis_refresh":
        hypothesis_result = BetaHypothesisService.refresh_hypotheses()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_hypothesis_refresh",
            job_type="research_registry",
            status="SUCCESS",
            details=hypothesis_result,
        )
        message = (
            "Hypothesis refresh completed: "
            f"{hypothesis_result.get('refreshed', 0)} refreshed, "
            f"{hypothesis_result.get('changed', 0)} changed."
        )
    elif action == "run_evaluation":
        evaluation_result = BetaEvaluationService.run_live_evaluation()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_evaluation",
            job_type="evaluation",
            status="SUCCESS",
            details=evaluation_result,
        )
        message = (
            "Live evaluation completed with "
            f"{evaluation_result.get('labeled_scores', 0)} labeled observations."
        )
    elif action == "run_training":
        training_result = BetaTrainingService.train_daily_challenger()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_training",
            job_type="training",
            status="SUCCESS" if training_result.get("trained") else "SKIPPED",
            details=training_result,
        )
        message = (
            "Training "
            + (
                f"stored model {training_result.get('version_code')} and strategy {training_result.get('strategy_version_id')}."
                if training_result.get("trained")
                else f"skipped: {training_result.get('reason', 'unknown')}."
            )
        )
    elif action == "run_news_sync":
        news_result = BetaNewsService.ingest_active_sources()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_news_sync",
            job_type="news",
            status="SUCCESS",
            details=news_result,
        )
        message = (
            "News sync stored "
            f"{news_result.get('articles_stored', 0)} articles and "
            f"{news_result.get('links_stored', 0)} links."
        )
    elif action == "run_backfill":
        backfill_result = BetaCorpusService.backfill_market_corpus(include_benchmarks=True)
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_corpus_backfill",
            job_type="corpus",
            status="SUCCESS",
            details=backfill_result,
        )
        message = (
            "Corpus backfill added "
            f"{backfill_result.get('instrument_bars_added', 0)} instrument bars and "
            f"{backfill_result.get('benchmarks_added', 0)} benchmark bars."
        )
    elif action == "run_filing_sync":
        filing_result = BetaFilingService.ingest_active_sources()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_filing_sync",
            job_type="filings",
            status="SUCCESS",
            details=filing_result,
        )
        message = (
            "Official release sync stored "
            f"{filing_result.get('events_stored', 0)} events and "
            f"{filing_result.get('links_stored', 0)} links."
        )
    elif action == "run_review":
        review_result = BetaReviewService.run_potential_gains_review()
        BetaRuntimeService.record_job_run(
            job_name="beta_manual_review",
            job_type="review",
            status="SUCCESS",
            details=review_result,
        )
        message = (
            "Potential gains review completed with "
            f"{review_result.get('findings', 0)} findings."
        )

    BetaRuntimeService.sync_system_status(
        core_db_path=core_db_path,
        beta_db_path=beta_db_path,
        settings=settings,
    )
    BetaRuntimeService.record_notification(
        notification_type="manual_control",
        severity="INFO",
        title="Beta control applied",
        message_text=message,
    )
    return RedirectResponse(
        f"/paper-trading-beta/health?msg={message.replace(' ', '+')}",
        status_code=303,
    )
