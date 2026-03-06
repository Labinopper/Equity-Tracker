"""
ScenarioSnapshotRepository - persisted Scenario Lab snapshots.
"""

from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ScenarioSnapshot


class ScenarioSnapshotRepository:
    def __init__(self, session: Session) -> None:
        self._s = session

    def upsert(
        self,
        *,
        snapshot_id: str,
        name: str,
        as_of_date: date,
        execution_mode: str,
        price_shock_pct: str,
        payload_json: str,
        input_snapshot_json: str | None = None,
    ) -> ScenarioSnapshot:
        row = self._s.get(ScenarioSnapshot, snapshot_id)
        if row is None:
            row = ScenarioSnapshot(
                id=snapshot_id,
                name=name,
                as_of_date=as_of_date,
                execution_mode=execution_mode,
                price_shock_pct=price_shock_pct,
                payload_json=payload_json,
                input_snapshot_json=input_snapshot_json,
            )
            self._s.add(row)
            return row

        row.name = name
        row.as_of_date = as_of_date
        row.execution_mode = execution_mode
        row.price_shock_pct = price_shock_pct
        row.payload_json = payload_json
        row.input_snapshot_json = input_snapshot_json
        return row

    def get_by_id(self, snapshot_id: str) -> ScenarioSnapshot | None:
        return self._s.get(ScenarioSnapshot, snapshot_id)

    def list_recent(self, *, limit: int = 20) -> list[ScenarioSnapshot]:
        if limit <= 0:
            return []
        stmt = (
            select(ScenarioSnapshot)
            .order_by(ScenarioSnapshot.created_at.desc(), ScenarioSnapshot.id.desc())
            .limit(limit)
        )
        return list(self._s.scalars(stmt).all())

    def trim(self, *, max_rows: int) -> int:
        if max_rows < 0:
            max_rows = 0
        keep_ids = set(
            self._s.scalars(
                select(ScenarioSnapshot.id)
                .order_by(ScenarioSnapshot.created_at.desc(), ScenarioSnapshot.id.desc())
                .limit(max_rows)
            ).all()
        )
        stale_rows = list(
            self._s.scalars(
                select(ScenarioSnapshot).where(~ScenarioSnapshot.id.in_(keep_ids))
            ).all()
        ) if keep_ids else list(self._s.scalars(select(ScenarioSnapshot)).all())
        for row in stale_rows:
            self._s.delete(row)
        return len(stale_rows)
