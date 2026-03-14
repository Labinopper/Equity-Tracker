"""Govern and score core research hypothesis families for the beta."""

from __future__ import annotations

import json

from sqlalchemy import desc, select

from ..context import BetaContext
from ..db.models import (
    BetaHypothesis,
    BetaHypothesisBeliefState,
    BetaHypothesisDefinition,
    BetaHypothesisEvent,
    BetaHypothesisFamily,
)

_DEFAULT_HYPOTHESES = (
    {
        "code": "TREND_PULLBACK_RECOVERY",
        "title": "Trend continuation with pullback recovery",
        "notes": (
            "Price/volume-led family looking for continuation after a controlled pullback "
            "and re-acceleration."
        ),
    },
    {
        "code": "CATALYST_CONFIRMATION",
        "title": "Catalyst plus confirmation",
        "notes": (
            "Event-led family that waits for official/news catalyst presence and then "
            "requires market confirmation before expression."
        ),
    },
)
_BASE_NOTES_BY_CODE = {row["code"]: row["notes"] for row in _DEFAULT_HYPOTHESES}


def _safe_float(value) -> float:
    try:
        return float(value or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _score_hypothesis(
    *,
    candidate_count: int,
    promoted_count: int,
    open_positions: int,
    closed_positions: int,
    avg_confidence: float,
    avg_edge: float,
    win_rate_pct: float,
    avg_pnl_pct: float,
) -> float:
    activity_score = min(25.0, (candidate_count * 2.5) + (promoted_count * 4.0) + (closed_positions * 4.0))
    confidence_score = min(20.0, avg_confidence * 20.0)
    edge_score = min(20.0, avg_edge * 20.0)
    win_score = min(20.0, max(0.0, win_rate_pct / 5.0)) if closed_positions else 0.0
    pnl_score = min(15.0, max(0.0, (avg_pnl_pct + 2.0) * 3.0)) if closed_positions else 0.0
    live_score = min(10.0, open_positions * 3.0)
    return round(activity_score + confidence_score + edge_score + win_score + pnl_score + live_score, 2)


def _next_status(
    *,
    candidate_count: int,
    promoted_count: int,
    closed_positions: int,
    avg_confidence: float,
    avg_edge: float,
    win_rate_pct: float,
    avg_pnl_pct: float,
) -> str:
    if closed_positions >= 3 and win_rate_pct < 35.0 and avg_pnl_pct <= -0.5:
        return "SUSPENDED"
    if (
        (closed_positions >= 2 and win_rate_pct >= 50.0 and avg_pnl_pct > 0.0)
        or (promoted_count >= 3 and avg_confidence >= 0.68 and avg_edge >= 0.28)
        or (candidate_count >= 5 and avg_confidence >= 0.72 and avg_edge >= 0.34)
    ):
        return "PROMOTED"
    return "RESEARCH"


class BetaHypothesisService:
    """Helpers for the governed hypothesis registry."""

    @staticmethod
    def ensure_default_hypotheses() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"added": 0}

        added = 0
        with BetaContext.write_session() as sess:
            for row in _DEFAULT_HYPOTHESES:
                existing = sess.scalar(
                    select(BetaHypothesis).where(BetaHypothesis.code == row["code"])
                )
                if existing is not None:
                    continue
                sess.add(
                    BetaHypothesis(
                        code=row["code"],
                        title=row["title"],
                        status="RESEARCH",
                        notes=row["notes"],
                        auto_promoted=False,
                    )
                )
                added += 1
        return {"added": added}

    @staticmethod
    def classify_hypothesis_code(
        *,
        direction: str,
        news_context: dict[str, object],
        filing_context: dict[str, object],
    ) -> str:
        news_count = int(news_context.get("count") or 0)
        filing_count = int(filing_context.get("count") or 0)
        news_sentiment = abs(_safe_float(news_context.get("avg_sentiment")))
        filing_sentiment = abs(_safe_float(filing_context.get("avg_sentiment")))

        catalyst_weight = (news_count * 1.0) + (filing_count * 2.0)
        if news_sentiment >= 0.4:
            catalyst_weight += 1.0
        if filing_sentiment >= 0.35:
            catalyst_weight += 1.0
        if direction in {"BEARISH", "RISK_OFF"} and (news_count or filing_count):
            catalyst_weight += 1.0

        if catalyst_weight >= 2.0:
            return "CATALYST_CONFIRMATION"
        return "TREND_PULLBACK_RECOVERY"

    @staticmethod
    def hypothesis_id_by_code(sess, code: str) -> str | None:
        hypothesis = sess.scalar(select(BetaHypothesis).where(BetaHypothesis.code == code))
        return hypothesis.id if hypothesis is not None else None

    @staticmethod
    def refresh_hypotheses() -> dict[str, object]:
        if not BetaContext.is_initialized():
            return {"refreshed": 0, "changed": 0}

        with BetaContext.write_session() as sess:
            family_rows = {
                row.family_code: row
                for row in sess.scalars(select(BetaHypothesisFamily)).all()
            }
            definition_rows = list(sess.scalars(select(BetaHypothesisDefinition)).all())
            belief_rows = {
                row.hypothesis_definition_id: row
                for row in sess.scalars(select(BetaHypothesisBeliefState)).all()
            }
            definitions_by_family: dict[str, list[BetaHypothesisDefinition]] = {}
            for definition in definition_rows:
                family = family_rows.get(
                    next(
                        (
                            family_code
                            for family_code, family_row in family_rows.items()
                            if family_row.id == definition.family_id
                        ),
                        "",
                    )
                )
                if family is None:
                    continue
                definitions_by_family.setdefault(family.family_code, []).append(definition)

            hypotheses = list(
                sess.scalars(select(BetaHypothesis).order_by(BetaHypothesis.code.asc())).all()
            )
            refreshed = 0
            changed = 0
            promoted = 0
            suspended = 0
            restored = 0
            summaries: list[dict[str, object]] = []
            changes_detail: list[dict[str, object]] = []

            for hypothesis in hypotheses:
                family = family_rows.get(hypothesis.code)
                family_definitions = definitions_by_family.get(hypothesis.code, [])
                definition_beliefs = [
                    belief_rows.get(definition.id)
                    for definition in family_definitions
                    if belief_rows.get(definition.id) is not None
                ]
                candidate_count = len(family_definitions)
                promoted_count = len([row for row in definition_beliefs if row.status == "VALIDATED"])
                promising_count = len([row for row in definition_beliefs if row.status == "PROMISING"])
                degraded_count = len([row for row in definition_beliefs if row.status in {"DEGRADED", "REJECTED"}])
                avg_confidence = (
                    sum(float(row.confidence_score or 0.0) for row in definition_beliefs) / len(definition_beliefs)
                    if definition_beliefs
                    else 0.0
                )
                avg_edge = (
                    sum(float(row.out_of_sample_strength or 0.0) for row in definition_beliefs) / len(definition_beliefs)
                    if definition_beliefs
                    else 0.0
                )
                avg_pnl_pct = (
                    sum(float(row.in_sample_strength or 0.0) for row in definition_beliefs) / len(definition_beliefs)
                    if definition_beliefs
                    else 0.0
                )
                win_rate_pct = (
                    sum(
                        100.0
                        for row in definition_beliefs
                        if float(row.out_of_sample_strength or 0.0) > 0.0
                    ) / len(definition_beliefs)
                    if definition_beliefs
                    else 0.0
                )
                evidence_score = round(avg_confidence * 100.0, 2)
                next_status = "RESEARCH"
                if promoted_count > 0:
                    next_status = "PROMOTED"
                elif family is not None and family.status != "ACTIVE":
                    next_status = "SUSPENDED"
                elif degraded_count and not promising_count:
                    next_status = "SUSPENDED"
                evidence_summary = (
                    f"{candidate_count} definitions, {promoted_count} validated, "
                    f"{promising_count} promising, {degraded_count} degraded/rejected, "
                    f"avg belief {avg_confidence:.2f}, avg OOS strength {avg_edge:.2f}."
                )
                previous_status = str(hypothesis.status or "RESEARCH")
                previous_evidence = _safe_float(hypothesis.evidence_score)
                base_note = _BASE_NOTES_BY_CODE.get(hypothesis.code)

                hypothesis.evidence_score = f"{evidence_score:.2f}"
                hypothesis.notes = (
                    f"{base_note} Current evidence: {evidence_summary}"
                    if base_note
                    else evidence_summary
                )
                hypothesis.auto_promoted = next_status == "PROMOTED"
                hypothesis.status = next_status
                refreshed += 1

                if previous_status != next_status:
                    changed += 1
                    if next_status == "PROMOTED":
                        promoted += 1
                    elif next_status == "SUSPENDED":
                        suspended += 1
                    elif previous_status == "SUSPENDED" and next_status == "RESEARCH":
                        restored += 1

                    sess.add(
                        BetaHypothesisEvent(
                            hypothesis_id=hypothesis.id,
                            event_type="STATUS_CHANGED",
                            status_before=previous_status,
                            status_after=next_status,
                            message_text=(
                                f"{hypothesis.title} moved from {previous_status} to {next_status} "
                                f"with evidence score {evidence_score:.2f}."
                            ),
                            payload_json=json.dumps(
                                {
                                    "definition_count": candidate_count,
                                    "validated_count": promoted_count,
                                    "promising_count": promising_count,
                                    "degraded_count": degraded_count,
                                    "avg_confidence": round(avg_confidence, 4),
                                    "avg_out_of_sample_strength": round(avg_edge, 4),
                                    "avg_in_sample_strength": round(avg_pnl_pct, 2),
                                    "evidence_score": evidence_score,
                                },
                                sort_keys=True,
                            ),
                        )
                    )
                    changes_detail.append(
                        {
                            "hypothesis_id": hypothesis.id,
                            "title": hypothesis.title,
                            "status_before": previous_status,
                            "status_after": next_status,
                            "evidence_score": evidence_score,
                        }
                    )
                elif abs(evidence_score - previous_evidence) >= 10.0:
                    sess.add(
                        BetaHypothesisEvent(
                            hypothesis_id=hypothesis.id,
                            event_type="REFRESHED",
                            status_before=previous_status,
                            status_after=next_status,
                            message_text=(
                                f"{hypothesis.title} evidence shifted to {evidence_score:.2f} "
                                f"without a status change."
                            ),
                            payload_json=json.dumps(
                                {
                                    "definition_count": candidate_count,
                                    "validated_count": promoted_count,
                                    "promising_count": promising_count,
                                    "degraded_count": degraded_count,
                                    "avg_confidence": round(avg_confidence, 4),
                                    "avg_out_of_sample_strength": round(avg_edge, 4),
                                    "avg_in_sample_strength": round(avg_pnl_pct, 2),
                                    "evidence_score": evidence_score,
                                    "previous_evidence_score": previous_evidence,
                                },
                                sort_keys=True,
                            ),
                        )
                    )

                summaries.append(
                    {
                        "code": hypothesis.code,
                        "status": next_status,
                        "evidence_score": evidence_score,
                        "definition_count": candidate_count,
                        "validated_count": promoted_count,
                        "promising_count": promising_count,
                        "degraded_count": degraded_count,
                        "avg_belief_confidence": round(avg_confidence, 4),
                        "avg_out_of_sample_strength": round(avg_edge, 4),
                    }
                )

            return {
                "refreshed": refreshed,
                "changed": changed,
                "promoted": promoted,
                "suspended": suspended,
                "restored": restored,
                "changes_detail": changes_detail,
                "summaries": summaries,
            }
