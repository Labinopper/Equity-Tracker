"""Strategy-version governance for the active beta scorer."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import desc, select

from ..db.models import BetaLabelDefinition, BetaStrategyEvent, BetaStrategyVersion


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class BetaStrategyService:
    """Create governed strategy versions around validated model changes."""

    _STRATEGY_NAME = "adaptive_daily_long"

    @staticmethod
    def get_active_strategy(sess) -> BetaStrategyVersion | None:
        return sess.scalar(
            select(BetaStrategyVersion)
            .where(BetaStrategyVersion.is_active.is_(True))
            .order_by(desc(BetaStrategyVersion.activated_at), desc(BetaStrategyVersion.created_at))
            .limit(1)
        )

    @staticmethod
    def sync_model_strategy(
        *,
        sess,
        model,
        label_definition: BetaLabelDefinition | None,
        validation_metrics: dict[str, object],
        activate: bool,
    ) -> BetaStrategyVersion:
        version_code = str(model.version_code)
        strategy = sess.scalar(
            select(BetaStrategyVersion).where(
                BetaStrategyVersion.strategy_name == BetaStrategyService._STRATEGY_NAME,
                BetaStrategyVersion.version_code == version_code,
            )
        )
        status = "ACTIVE" if activate else "CHALLENGER"
        min_confidence_score = 0.55
        min_edge_score = 0.20
        sign_accuracy = float(validation_metrics.get("avg_validation_sign_accuracy_pct") or 0.0)
        avg_return = float(validation_metrics.get("avg_validation_return_pct") or 0.0)
        if sign_accuracy >= 62.0 and avg_return > 0.10:
            min_confidence_score = 0.52
            min_edge_score = 0.16
        elif sign_accuracy < 55.0:
            min_confidence_score = 0.60
            min_edge_score = 0.24

        if strategy is None:
            strategy = BetaStrategyVersion(
                strategy_name=BetaStrategyService._STRATEGY_NAME,
                version_code=version_code,
                status=status,
                is_active=False,
                model_version_id=model.id,
                label_definition_id=label_definition.id if label_definition is not None else None,
                min_confidence_score=min_confidence_score,
                min_expected_edge_score=min_edge_score,
                capital_weight_mode="CONFIDENCE_EDGE",
                notes_json=json.dumps(
                    {
                        "model_name": model.model_name,
                        "algorithm": model.algorithm,
                        "validation": validation_metrics,
                    },
                    sort_keys=True,
                ),
            )
            sess.add(strategy)
            sess.flush()
            sess.add(
                BetaStrategyEvent(
                    strategy_version_id=strategy.id,
                    event_type="CREATED",
                    status_before=None,
                    status_after=status,
                    message_text=f"Created strategy version {version_code} for model {model.model_name}.",
                    payload_json=strategy.notes_json,
                )
            )
        else:
            strategy.model_version_id = model.id
            strategy.label_definition_id = label_definition.id if label_definition is not None else None
            strategy.min_confidence_score = min_confidence_score
            strategy.min_expected_edge_score = min_edge_score
            strategy.notes_json = json.dumps(
                {
                    "model_name": model.model_name,
                    "algorithm": model.algorithm,
                    "validation": validation_metrics,
                },
                sort_keys=True,
            )

        previous_active = BetaStrategyService.get_active_strategy(sess)
        if activate:
            if previous_active is not None and previous_active.id != strategy.id:
                previous_status = previous_active.status
                previous_active.is_active = False
                previous_active.status = "SUSPENDED"
                sess.add(
                    BetaStrategyEvent(
                        strategy_version_id=previous_active.id,
                        event_type="DEACTIVATED",
                        status_before=previous_status,
                        status_after=previous_active.status,
                        message_text=(
                            f"Strategy {previous_active.version_code} was deactivated in favour of {version_code}."
                        ),
                        payload_json=json.dumps({"replacement_version": version_code}, sort_keys=True),
                    )
                )
            previous_status = strategy.status
            strategy.status = "ACTIVE"
            strategy.is_active = True
            strategy.activated_at = _utcnow()
            sess.add(
                BetaStrategyEvent(
                    strategy_version_id=strategy.id,
                    event_type="ACTIVATED",
                    status_before=previous_status,
                    status_after=strategy.status,
                    message_text=f"Strategy version {version_code} became active.",
                    payload_json=json.dumps(validation_metrics, sort_keys=True),
                )
            )
        else:
            strategy.is_active = False
            strategy.status = "CHALLENGER"
            sess.add(
                BetaStrategyEvent(
                    strategy_version_id=strategy.id,
                    event_type="VALIDATED",
                    status_before=strategy.status,
                    status_after="CHALLENGER",
                    message_text=f"Strategy version {version_code} stored as challenger.",
                    payload_json=json.dumps(validation_metrics, sort_keys=True),
                )
            )
        return strategy
