"""Settings persisted alongside the separate beta research database."""

from __future__ import annotations

import json
from pathlib import Path

from .paths import resolve_beta_settings_path

_VALID_MODES = (
    "OFF",
    "OBSERVE_ONLY",
    "SHADOW_ONLY",
    "DEMO_NO_LEARN",
    "FULL_INTERNAL_BETA",
)


class BetaSettings:
    """Runtime and operational controls for the paper-trading beta."""

    enabled: bool
    mode: str
    web_ui_enabled: bool
    auto_start_supervisor: bool
    observation_enabled: bool
    learning_enabled: bool
    shadow_scoring_enabled: bool
    demo_execution_enabled: bool
    news_enabled: bool
    filings_enabled: bool
    paid_news_enrichment_enabled: bool
    gpu_enabled: bool
    training_enabled: bool
    validation_enabled: bool
    incremental_learning_enabled: bool
    background_jobs_enabled: bool
    max_cpu_workers: int
    max_concurrent_heavy_jobs: int
    max_training_minutes_per_run: int
    retrain_min_new_observations: int
    max_memory_mb: int
    max_memory_pct: int
    hypothesis_discovery_enabled: bool
    hypothesis_discovery_history_years: int
    hypothesis_discovery_universe_cap: int
    hypothesis_discovery_template_limit: int
    hypothesis_discovery_variant_cap: int
    hypothesis_discovery_max_promotions_per_run: int
    hypothesis_discovery_min_support: int
    hypothesis_discovery_max_condition_count: int
    intraday_execution_enabled: bool
    intraday_event_trigger_enabled: bool
    intraday_held_symbol_cadence_minutes: int
    intraday_active_thesis_cadence_minutes: int
    intraday_watchlist_general_cap: int
    intraday_learning_symbol_budget: int
    intraday_history_lookback_minutes: int
    intraday_priority_held_weight_pct: int
    intraday_priority_active_thesis_weight_pct: int
    intraday_priority_general_weight_pct: int
    intraday_volatility_expansion_threshold_pct: float
    intraday_gap_event_threshold_pct: float
    intraday_large_move_event_threshold_pct: float
    intraday_reversal_event_threshold_pct: float
    intraday_bar_fetch_enabled: bool
    intraday_bar_fetch_live_credits_budget: int
    intraday_bar_fetch_eod_credits_budget: int
    intraday_bar_backfill_enabled: bool
    intraday_bar_backfill_target_days: int
    intraday_bar_backfill_credits_budget: int
    instrument_statistics_enabled: bool
    instrument_statistics_refresh_days: int
    instrument_statistics_credits_budget: int
    training_window_start_local: str
    training_window_end_local: str
    research_quiet_hours_only: bool
    pause_on_startup: bool
    auto_shadow_enable: bool
    auto_demo_enable_when_ready: bool
    shadow_default_cadence_minutes: int
    auto_pause_entries_on_degradation: bool
    auto_resume_entries_on_recovery: bool
    market_hours_credit_buffer: int
    market_hours_live_data_priority_enabled: bool
    uk_equity_friction_bps: int
    us_equity_friction_bps: int
    fx_trade_friction_bps: int

    def __init__(self) -> None:
        self.enabled = True
        self.mode = "FULL_INTERNAL_BETA"
        self.web_ui_enabled = True
        self.auto_start_supervisor = True
        self.observation_enabled = True
        self.learning_enabled = True
        self.shadow_scoring_enabled = True
        self.demo_execution_enabled = True
        self.news_enabled = True
        self.filings_enabled = True
        self.paid_news_enrichment_enabled = True
        self.gpu_enabled = False
        self.training_enabled = True
        self.validation_enabled = True
        self.incremental_learning_enabled = True
        self.background_jobs_enabled = True
        self.max_cpu_workers = 1
        self.max_concurrent_heavy_jobs = 1
        self.max_training_minutes_per_run = 30
        self.retrain_min_new_observations = 500
        self.max_memory_mb = 1024
        self.max_memory_pct = 75
        self.hypothesis_discovery_enabled = True
        self.hypothesis_discovery_history_years = 5
        self.hypothesis_discovery_universe_cap = 250
        self.hypothesis_discovery_template_limit = 8
        self.hypothesis_discovery_variant_cap = 24
        self.hypothesis_discovery_max_promotions_per_run = 6
        self.hypothesis_discovery_min_support = 40
        self.hypothesis_discovery_max_condition_count = 4
        self.intraday_execution_enabled = True
        self.intraday_event_trigger_enabled = True
        self.intraday_held_symbol_cadence_minutes = 3
        self.intraday_active_thesis_cadence_minutes = 10
        self.intraday_watchlist_general_cap = 12
        self.intraday_learning_symbol_budget = 48
        self.intraday_history_lookback_minutes = 240
        self.intraday_priority_held_weight_pct = 60
        self.intraday_priority_active_thesis_weight_pct = 30
        self.intraday_priority_general_weight_pct = 10
        self.intraday_volatility_expansion_threshold_pct = 1.8
        self.intraday_gap_event_threshold_pct = 2.0
        self.intraday_large_move_event_threshold_pct = 2.5
        self.intraday_reversal_event_threshold_pct = 3.0
        self.intraday_bar_fetch_enabled = True
        self.intraday_bar_fetch_live_credits_budget = 30
        self.intraday_bar_fetch_eod_credits_budget = 20
        self.intraday_bar_backfill_enabled = True
        self.intraday_bar_backfill_target_days = 30
        self.intraday_bar_backfill_credits_budget = 30
        self.instrument_statistics_enabled = True
        self.instrument_statistics_refresh_days = 7
        self.instrument_statistics_credits_budget = 10
        self.training_window_start_local = "22:00"
        self.training_window_end_local = "06:00"
        self.research_quiet_hours_only = True
        self.pause_on_startup = False
        self.auto_shadow_enable = True
        self.auto_demo_enable_when_ready = True
        self.shadow_default_cadence_minutes = 5
        self.auto_pause_entries_on_degradation = True
        self.auto_resume_entries_on_recovery = True
        self.market_hours_credit_buffer = 5
        self.market_hours_live_data_priority_enabled = True
        self.uk_equity_friction_bps = 18
        self.us_equity_friction_bps = 25
        self.fx_trade_friction_bps = 12
        self._settings_path = Path("beta.settings.json")

    @classmethod
    def load(cls, beta_db_path: Path) -> "BetaSettings":
        obj = cls()
        obj._settings_path = resolve_beta_settings_path(beta_db_path)
        if obj._settings_path.exists():
            try:
                data = json.loads(obj._settings_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return obj
            obj._apply(data)
        return obj

    @classmethod
    def defaults_for(cls, beta_db_path: Path) -> "BetaSettings":
        obj = cls()
        obj._settings_path = resolve_beta_settings_path(beta_db_path)
        return obj

    def save(self) -> None:
        data = {
            "enabled": self.enabled,
            "mode": self.mode,
            "web_ui_enabled": self.web_ui_enabled,
            "auto_start_supervisor": self.auto_start_supervisor,
            "observation_enabled": self.observation_enabled,
            "learning_enabled": self.learning_enabled,
            "shadow_scoring_enabled": self.shadow_scoring_enabled,
            "demo_execution_enabled": self.demo_execution_enabled,
            "news_enabled": self.news_enabled,
            "filings_enabled": self.filings_enabled,
            "paid_news_enrichment_enabled": self.paid_news_enrichment_enabled,
            "gpu_enabled": self.gpu_enabled,
            "training_enabled": self.training_enabled,
            "validation_enabled": self.validation_enabled,
            "incremental_learning_enabled": self.incremental_learning_enabled,
            "background_jobs_enabled": self.background_jobs_enabled,
            "max_cpu_workers": self.max_cpu_workers,
            "max_concurrent_heavy_jobs": self.max_concurrent_heavy_jobs,
            "max_training_minutes_per_run": self.max_training_minutes_per_run,
            "retrain_min_new_observations": self.retrain_min_new_observations,
            "max_memory_mb": self.max_memory_mb,
            "max_memory_pct": self.max_memory_pct,
            "hypothesis_discovery_enabled": self.hypothesis_discovery_enabled,
            "hypothesis_discovery_history_years": self.hypothesis_discovery_history_years,
            "hypothesis_discovery_universe_cap": self.hypothesis_discovery_universe_cap,
            "hypothesis_discovery_template_limit": self.hypothesis_discovery_template_limit,
            "hypothesis_discovery_variant_cap": self.hypothesis_discovery_variant_cap,
            "hypothesis_discovery_max_promotions_per_run": self.hypothesis_discovery_max_promotions_per_run,
            "hypothesis_discovery_min_support": self.hypothesis_discovery_min_support,
            "hypothesis_discovery_max_condition_count": self.hypothesis_discovery_max_condition_count,
            "intraday_execution_enabled": self.intraday_execution_enabled,
            "intraday_event_trigger_enabled": self.intraday_event_trigger_enabled,
            "intraday_held_symbol_cadence_minutes": self.intraday_held_symbol_cadence_minutes,
            "intraday_active_thesis_cadence_minutes": self.intraday_active_thesis_cadence_minutes,
            "intraday_watchlist_general_cap": self.intraday_watchlist_general_cap,
            "intraday_learning_symbol_budget": self.intraday_learning_symbol_budget,
            "intraday_history_lookback_minutes": self.intraday_history_lookback_minutes,
            "intraday_priority_held_weight_pct": self.intraday_priority_held_weight_pct,
            "intraday_priority_active_thesis_weight_pct": self.intraday_priority_active_thesis_weight_pct,
            "intraday_priority_general_weight_pct": self.intraday_priority_general_weight_pct,
            "intraday_volatility_expansion_threshold_pct": self.intraday_volatility_expansion_threshold_pct,
            "intraday_gap_event_threshold_pct": self.intraday_gap_event_threshold_pct,
            "intraday_large_move_event_threshold_pct": self.intraday_large_move_event_threshold_pct,
            "intraday_reversal_event_threshold_pct": self.intraday_reversal_event_threshold_pct,
            "intraday_bar_fetch_enabled": self.intraday_bar_fetch_enabled,
            "intraday_bar_fetch_live_credits_budget": self.intraday_bar_fetch_live_credits_budget,
            "intraday_bar_fetch_eod_credits_budget": self.intraday_bar_fetch_eod_credits_budget,
            "intraday_bar_backfill_enabled": self.intraday_bar_backfill_enabled,
            "intraday_bar_backfill_target_days": self.intraday_bar_backfill_target_days,
            "intraday_bar_backfill_credits_budget": self.intraday_bar_backfill_credits_budget,
            "instrument_statistics_enabled": self.instrument_statistics_enabled,
            "instrument_statistics_refresh_days": self.instrument_statistics_refresh_days,
            "instrument_statistics_credits_budget": self.instrument_statistics_credits_budget,
            "training_window_start_local": self.training_window_start_local,
            "training_window_end_local": self.training_window_end_local,
            "research_quiet_hours_only": self.research_quiet_hours_only,
            "pause_on_startup": self.pause_on_startup,
            "auto_shadow_enable": self.auto_shadow_enable,
            "auto_demo_enable_when_ready": self.auto_demo_enable_when_ready,
            "shadow_default_cadence_minutes": self.shadow_default_cadence_minutes,
            "auto_pause_entries_on_degradation": self.auto_pause_entries_on_degradation,
            "auto_resume_entries_on_recovery": self.auto_resume_entries_on_recovery,
            "market_hours_credit_buffer": self.market_hours_credit_buffer,
            "market_hours_live_data_priority_enabled": self.market_hours_live_data_priority_enabled,
            "uk_equity_friction_bps": self.uk_equity_friction_bps,
            "us_equity_friction_bps": self.us_equity_friction_bps,
            "fx_trade_friction_bps": self.fx_trade_friction_bps,
        }
        self._settings_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    @property
    def settings_path(self) -> Path:
        return self._settings_path

    def _apply(self, data: dict) -> None:
        self.enabled = bool(data.get("enabled", self.enabled))
        self.mode = _safe_mode(data.get("mode", self.mode))
        self.web_ui_enabled = bool(data.get("web_ui_enabled", self.web_ui_enabled))
        self.auto_start_supervisor = bool(data.get("auto_start_supervisor", self.auto_start_supervisor))
        self.observation_enabled = bool(data.get("observation_enabled", self.observation_enabled))
        self.learning_enabled = bool(data.get("learning_enabled", self.learning_enabled))
        self.shadow_scoring_enabled = bool(data.get("shadow_scoring_enabled", self.shadow_scoring_enabled))
        self.demo_execution_enabled = bool(data.get("demo_execution_enabled", self.demo_execution_enabled))
        self.news_enabled = bool(data.get("news_enabled", self.news_enabled))
        self.filings_enabled = bool(data.get("filings_enabled", self.filings_enabled))
        self.paid_news_enrichment_enabled = bool(
            data.get("paid_news_enrichment_enabled", self.paid_news_enrichment_enabled)
        )
        self.gpu_enabled = bool(data.get("gpu_enabled", self.gpu_enabled))
        self.training_enabled = bool(data.get("training_enabled", self.training_enabled))
        self.validation_enabled = bool(data.get("validation_enabled", self.validation_enabled))
        self.incremental_learning_enabled = bool(
            data.get("incremental_learning_enabled", self.incremental_learning_enabled)
        )
        self.background_jobs_enabled = bool(data.get("background_jobs_enabled", self.background_jobs_enabled))
        self.max_cpu_workers = max(1, _safe_int(data.get("max_cpu_workers"), self.max_cpu_workers))
        self.max_concurrent_heavy_jobs = max(
            1, _safe_int(data.get("max_concurrent_heavy_jobs"), self.max_concurrent_heavy_jobs)
        )
        self.max_training_minutes_per_run = max(
            1, _safe_int(data.get("max_training_minutes_per_run"), self.max_training_minutes_per_run)
        )
        self.retrain_min_new_observations = max(
            1, _safe_int(data.get("retrain_min_new_observations"), self.retrain_min_new_observations)
        )
        self.max_memory_mb = max(128, _safe_int(data.get("max_memory_mb"), self.max_memory_mb))
        self.max_memory_pct = min(95, max(50, _safe_int(data.get("max_memory_pct"), self.max_memory_pct)))
        self.hypothesis_discovery_enabled = bool(
            data.get("hypothesis_discovery_enabled", self.hypothesis_discovery_enabled)
        )
        self.hypothesis_discovery_history_years = max(
            1,
            _safe_int(
                data.get("hypothesis_discovery_history_years"),
                self.hypothesis_discovery_history_years,
            ),
        )
        self.hypothesis_discovery_universe_cap = max(
            25,
            _safe_int(
                data.get("hypothesis_discovery_universe_cap"),
                self.hypothesis_discovery_universe_cap,
            ),
        )
        self.hypothesis_discovery_template_limit = max(
            1,
            _safe_int(
                data.get("hypothesis_discovery_template_limit"),
                self.hypothesis_discovery_template_limit,
            ),
        )
        self.hypothesis_discovery_variant_cap = max(
            4,
            _safe_int(
                data.get("hypothesis_discovery_variant_cap"),
                self.hypothesis_discovery_variant_cap,
            ),
        )
        self.hypothesis_discovery_max_promotions_per_run = max(
            1,
            _safe_int(
                data.get("hypothesis_discovery_max_promotions_per_run"),
                self.hypothesis_discovery_max_promotions_per_run,
            ),
        )
        self.hypothesis_discovery_min_support = max(
            10,
            _safe_int(
                data.get("hypothesis_discovery_min_support"),
                self.hypothesis_discovery_min_support,
            ),
        )
        self.hypothesis_discovery_max_condition_count = max(
            1,
            _safe_int(
                data.get("hypothesis_discovery_max_condition_count"),
                self.hypothesis_discovery_max_condition_count,
            ),
        )
        self.intraday_execution_enabled = bool(
            data.get("intraday_execution_enabled", self.intraday_execution_enabled)
        )
        self.intraday_event_trigger_enabled = bool(
            data.get("intraday_event_trigger_enabled", self.intraday_event_trigger_enabled)
        )
        self.intraday_held_symbol_cadence_minutes = max(
            1,
            _safe_int(
                data.get("intraday_held_symbol_cadence_minutes"),
                self.intraday_held_symbol_cadence_minutes,
            ),
        )
        self.intraday_active_thesis_cadence_minutes = max(
            self.intraday_held_symbol_cadence_minutes,
            _safe_int(
                data.get("intraday_active_thesis_cadence_minutes"),
                self.intraday_active_thesis_cadence_minutes,
            ),
        )
        self.intraday_watchlist_general_cap = max(
            0,
            _safe_int(
                data.get("intraday_watchlist_general_cap"),
                self.intraday_watchlist_general_cap,
            ),
        )
        self.intraday_learning_symbol_budget = max(
            3,
            _safe_int(
                data.get("intraday_learning_symbol_budget"),
                self.intraday_learning_symbol_budget,
            ),
        )
        self.intraday_history_lookback_minutes = max(
            30,
            _safe_int(
                data.get("intraday_history_lookback_minutes"),
                self.intraday_history_lookback_minutes,
            ),
        )
        self.intraday_priority_held_weight_pct = max(
            0,
            _safe_int(
                data.get("intraday_priority_held_weight_pct"),
                self.intraday_priority_held_weight_pct,
            ),
        )
        self.intraday_priority_active_thesis_weight_pct = max(
            0,
            _safe_int(
                data.get("intraday_priority_active_thesis_weight_pct"),
                self.intraday_priority_active_thesis_weight_pct,
            ),
        )
        self.intraday_priority_general_weight_pct = max(
            0,
            _safe_int(
                data.get("intraday_priority_general_weight_pct"),
                self.intraday_priority_general_weight_pct,
            ),
        )
        total_priority_weight = (
            self.intraday_priority_held_weight_pct
            + self.intraday_priority_active_thesis_weight_pct
            + self.intraday_priority_general_weight_pct
        )
        if total_priority_weight <= 0:
            self.intraday_priority_held_weight_pct = 60
            self.intraday_priority_active_thesis_weight_pct = 30
            self.intraday_priority_general_weight_pct = 10
        self.intraday_volatility_expansion_threshold_pct = max(
            0.5,
            _safe_float(
                data.get("intraday_volatility_expansion_threshold_pct"),
                self.intraday_volatility_expansion_threshold_pct,
            ),
        )
        self.intraday_gap_event_threshold_pct = max(
            0.5,
            _safe_float(
                data.get("intraday_gap_event_threshold_pct"),
                self.intraday_gap_event_threshold_pct,
            ),
        )
        self.intraday_large_move_event_threshold_pct = max(
            0.5,
            _safe_float(
                data.get("intraday_large_move_event_threshold_pct"),
                self.intraday_large_move_event_threshold_pct,
            ),
        )
        self.intraday_reversal_event_threshold_pct = max(
            0.5,
            _safe_float(
                data.get("intraday_reversal_event_threshold_pct"),
                self.intraday_reversal_event_threshold_pct,
            ),
        )
        self.intraday_bar_fetch_enabled = bool(
            data.get("intraday_bar_fetch_enabled", self.intraday_bar_fetch_enabled)
        )
        self.intraday_bar_fetch_live_credits_budget = max(
            1, _safe_int(data.get("intraday_bar_fetch_live_credits_budget"), self.intraday_bar_fetch_live_credits_budget)
        )
        self.intraday_bar_fetch_eod_credits_budget = max(
            1, _safe_int(data.get("intraday_bar_fetch_eod_credits_budget"), self.intraday_bar_fetch_eod_credits_budget)
        )
        self.intraday_bar_backfill_enabled = bool(
            data.get("intraday_bar_backfill_enabled", self.intraday_bar_backfill_enabled)
        )
        self.intraday_bar_backfill_target_days = max(
            1, _safe_int(data.get("intraday_bar_backfill_target_days"), self.intraday_bar_backfill_target_days)
        )
        self.intraday_bar_backfill_credits_budget = max(
            1, _safe_int(data.get("intraday_bar_backfill_credits_budget"), self.intraday_bar_backfill_credits_budget)
        )
        self.instrument_statistics_enabled = bool(
            data.get("instrument_statistics_enabled", self.instrument_statistics_enabled)
        )
        self.instrument_statistics_refresh_days = max(
            1, _safe_int(data.get("instrument_statistics_refresh_days"), self.instrument_statistics_refresh_days)
        )
        self.instrument_statistics_credits_budget = max(
            1, _safe_int(data.get("instrument_statistics_credits_budget"), self.instrument_statistics_credits_budget)
        )
        self.training_window_start_local = str(
            data.get("training_window_start_local", self.training_window_start_local)
        )
        self.training_window_end_local = str(
            data.get("training_window_end_local", self.training_window_end_local)
        )
        self.research_quiet_hours_only = bool(
            data.get("research_quiet_hours_only", self.research_quiet_hours_only)
        )
        self.pause_on_startup = bool(data.get("pause_on_startup", self.pause_on_startup))
        self.auto_shadow_enable = bool(data.get("auto_shadow_enable", self.auto_shadow_enable))
        self.auto_demo_enable_when_ready = bool(
            data.get("auto_demo_enable_when_ready", self.auto_demo_enable_when_ready)
        )
        self.shadow_default_cadence_minutes = max(
            1, _safe_int(data.get("shadow_default_cadence_minutes"), self.shadow_default_cadence_minutes)
        )
        self.auto_pause_entries_on_degradation = bool(
            data.get("auto_pause_entries_on_degradation", self.auto_pause_entries_on_degradation)
        )
        self.auto_resume_entries_on_recovery = bool(
            data.get("auto_resume_entries_on_recovery", self.auto_resume_entries_on_recovery)
        )
        self.market_hours_credit_buffer = max(
            0, _safe_int(data.get("market_hours_credit_buffer"), self.market_hours_credit_buffer)
        )
        self.market_hours_live_data_priority_enabled = bool(
            data.get(
                "market_hours_live_data_priority_enabled",
                self.market_hours_live_data_priority_enabled,
            )
        )
        self.uk_equity_friction_bps = max(
            0, _safe_int(data.get("uk_equity_friction_bps"), self.uk_equity_friction_bps)
        )
        self.us_equity_friction_bps = max(
            0, _safe_int(data.get("us_equity_friction_bps"), self.us_equity_friction_bps)
        )
        self.fx_trade_friction_bps = max(
            0, _safe_int(data.get("fx_trade_friction_bps"), self.fx_trade_friction_bps)
        )


def _safe_mode(value: object) -> str:
    candidate = str(value or "").strip().upper()
    if candidate in _VALID_MODES:
        return candidate
    return "FULL_INTERNAL_BETA"


def _safe_int(value: object, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: object, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback
