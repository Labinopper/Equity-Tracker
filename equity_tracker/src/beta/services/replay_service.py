"""Replay-pack generation for beta audit and review workflows."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

from ..paths import resolve_beta_artifacts_dir
from ..state import get_beta_db_path
from .overview_service import BetaOverviewService


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaReplayService:
    """Build lightweight JSON replay packs under beta_artifacts/."""

    @staticmethod
    def _artifacts_dir() -> Path | None:
        beta_db_path = get_beta_db_path()
        if beta_db_path is None:
            return None
        artifacts_dir = resolve_beta_artifacts_dir(beta_db_path)
        artifacts_dir.mkdir(parents=True, exist_ok=True)
        return artifacts_dir

    @staticmethod
    def list_recent_packs(*, limit: int = 12) -> list[dict[str, object]]:
        artifacts_dir = BetaReplayService._artifacts_dir()
        if artifacts_dir is None or not artifacts_dir.exists():
            return []
        rows: list[dict[str, object]] = []
        for path in sorted(artifacts_dir.glob("*.json"), key=lambda item: item.stat().st_mtime, reverse=True)[:limit]:
            stat = path.stat()
            rows.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "size_bytes": stat.st_size,
                    "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).replace(tzinfo=None),
                }
            )
        return rows

    @staticmethod
    def _write_pack(filename: str, payload: dict[str, object]) -> Path | None:
        artifacts_dir = BetaReplayService._artifacts_dir()
        if artifacts_dir is None:
            return None
        payload = {
            "generated_at": _utcnow().isoformat(),
            **payload,
        }
        path = artifacts_dir / filename
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        return path

    @staticmethod
    def ensure_daily_dashboard_pack() -> dict[str, object]:
        artifacts_dir = BetaReplayService._artifacts_dir()
        if artifacts_dir is None:
            return {"created": False, "path": None}
        today = date.today().isoformat()
        filename = f"dashboard_replay_{today}.json"
        path = artifacts_dir / filename
        if path.exists():
            return {"created": False, "path": str(path)}
        dashboard = BetaOverviewService.get_dashboard()
        written = BetaReplayService._write_pack(
            filename,
            {
                "pack_type": "dashboard_daily",
                "dashboard": dashboard,
            },
        )
        return {"created": written is not None, "path": str(written) if written is not None else None}

    @staticmethod
    def build_focus_replay_pack() -> dict[str, object]:
        dashboard = BetaOverviewService.get_dashboard()
        timestamp = _utcnow().strftime("%Y%m%d%H%M%S")
        candidate_ids = [str(row["id"]) for row in (dashboard.get("watched_candidates") or [])[:3]]
        position_ids = [str(row["id"]) for row in (dashboard.get("active_positions") or [])[:3]]
        hypothesis_ids = [str(row["id"]) for row in (dashboard.get("hypotheses") or [])[:2]]

        payload = {
            "pack_type": "focus_replay",
            "dashboard": dashboard,
            "candidates": [BetaOverviewService.get_candidate_detail(row_id) for row_id in candidate_ids],
            "positions": [BetaOverviewService.get_trade_detail(row_id) for row_id in position_ids],
            "hypotheses": [BetaOverviewService.get_hypothesis_detail(row_id) for row_id in hypothesis_ids],
        }
        written = BetaReplayService._write_pack(f"focus_replay_{timestamp}.json", payload)
        return {
            "created": written is not None,
            "path": str(written) if written is not None else None,
            "candidate_count": len(candidate_ids),
            "position_count": len(position_ids),
            "hypothesis_count": len(hypothesis_ids),
        }
