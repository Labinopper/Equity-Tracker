"""Official release and filing ingestion for beta research."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import httpx
from sqlalchemy import select

from ..context import BetaContext
from ..db.models import (
    BetaFilingEvent,
    BetaFilingEventLink,
    BetaFilingIngestionRun,
    BetaFilingSource,
    BetaInstrument,
)
from .news_service import (
    _instrument_match_candidates,
    _parse_feed,
    _sentiment_for,
    _utcnow,
)

_DEFAULT_SOURCES = (
    {
        "source_name": "SEC Press Releases",
        "feed_url": "https://www.sec.gov/rss/press.xml",
        "market": "US",
    },
    {
        "source_name": "Companies House Updates",
        "feed_url": "https://www.gov.uk/government/organisations/companies-house.atom",
        "market": "UK",
    },
)


def _categorize_event(title: str, summary: str | None) -> str:
    text = f"{title} {summary or ''}".lower()
    if any(token in text for token in ("earnings", "results", "final results", "interim results", "annual results")):
        return "EARNINGS"
    if any(token in text for token in ("trading update", "guidance", "outlook", "sales update")):
        return "TRADING_UPDATE"
    if any(
        token in text
        for token in (
            "filing",
            "regulatory",
            "annual report",
            "10-k",
            "10-q",
            "8-k",
            "statement",
            "prospectus",
        )
    ):
        return "REGULATORY_FILING"
    if any(token in text for token in ("dividend", "buyback", "acquisition", "merger", "split", "capital return")):
        return "CORPORATE_ACTION"
    return "OFFICIAL_RELEASE"


class BetaFilingService:
    """Persist official release and filing metadata for catalyst research."""

    @staticmethod
    def ensure_default_sources() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"added": 0}

        added = 0
        with BetaContext.write_session() as sess:
            for row in _DEFAULT_SOURCES:
                existing = sess.scalar(
                    select(BetaFilingSource).where(
                        BetaFilingSource.source_name == row["source_name"],
                        BetaFilingSource.feed_url == row["feed_url"],
                    )
                )
                if existing is None:
                    sess.add(
                        BetaFilingSource(
                            source_name=row["source_name"],
                            feed_url=row["feed_url"],
                            market=row["market"],
                            source_type="OFFICIAL_FEED",
                            is_active=True,
                        )
                    )
                    added += 1
        return {"added": added}

    @staticmethod
    def ingest_active_sources(*, source_limit: int = 2, max_events_per_source: int = 20) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"sources_processed": 0, "events_stored": 0, "links_stored": 0}

        with BetaContext.read_session() as sess:
            sources = list(
                sess.scalars(
                    select(BetaFilingSource)
                    .where(BetaFilingSource.is_active.is_(True))
                    .order_by(BetaFilingSource.created_at.asc())
                    .limit(source_limit)
                ).all()
            )
        total_events = 0
        total_links = 0
        processed = 0
        for source in sources:
            result = BetaFilingService.ingest_source(source.id, max_events=max_events_per_source)
            total_events += int(result.get("stored_count", 0))
            total_links += int(result.get("linked_count", 0))
            processed += 1
        return {
            "sources_processed": processed,
            "events_stored": total_events,
            "links_stored": total_links,
        }

    @staticmethod
    def ingest_source(source_id: str, *, xml_text: str | None = None, max_events: int = 20) -> dict[str, int | str]:
        if not BetaContext.is_initialized():
            return {"stored_count": 0, "linked_count": 0, "status": "SKIPPED"}

        with BetaContext.read_session() as sess:
            source = sess.scalar(select(BetaFilingSource).where(BetaFilingSource.id == source_id))
            instruments = list(sess.scalars(select(BetaInstrument).where(BetaInstrument.is_active.is_(True))).all())
        if source is None:
            return {"stored_count": 0, "linked_count": 0, "status": "MISSING_SOURCE"}

        fetched_count = 0
        stored_count = 0
        linked_count = 0
        error_text = None
        if xml_text is None:
            try:
                with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                    response = client.get(source.feed_url)
                    response.raise_for_status()
                    xml_text = response.text
            except Exception as exc:
                error_text = str(exc)
                xml_text = None

        parsed_items = _parse_feed(xml_text or "") if xml_text else []
        fetched_count = min(len(parsed_items), max_events)
        now = _utcnow()

        with BetaContext.write_session() as sess:
            run = BetaFilingIngestionRun(
                source_id=source.id,
                status="SUCCESS" if error_text is None else "ERROR",
                fetched_count=fetched_count,
                stored_count=0,
                linked_count=0,
                error_text=error_text,
            )
            sess.add(run)
            sess.flush()

            cutoff = now - timedelta(days=21)
            for item in parsed_items[:max_events]:
                published_at = item["published_at"] if isinstance(item["published_at"], datetime) else None
                if published_at is not None and published_at < cutoff:
                    continue
                guid = str(item["guid"])
                existing = sess.scalar(
                    select(BetaFilingEvent).where(
                        BetaFilingEvent.source_id == source.id,
                        BetaFilingEvent.event_guid == guid,
                    )
                )
                if existing is not None:
                    continue
                title = str(item["title"])
                summary = item["summary"] if isinstance(item["summary"], str) else None
                sentiment_label, sentiment_score, tags = _sentiment_for(title, summary)
                category = _categorize_event(title, summary)
                matches = _instrument_match_candidates(title, summary, instruments)
                event = BetaFilingEvent(
                    source_id=source.id,
                    event_guid=guid,
                    title=title,
                    link_url=item["link"] if isinstance(item["link"], str) else None,
                    summary_text=summary,
                    published_at=published_at,
                    fetched_at=now,
                    event_category=category,
                    sentiment_label=sentiment_label,
                    sentiment_score=sentiment_score,
                    importance_score=min(1.0, 0.35 + (0.18 * len(matches)) + (0.12 * abs(sentiment_score))),
                    matched_symbols_json=json.dumps([instrument.symbol for instrument, _ in matches], sort_keys=True),
                    keyword_tags_json=json.dumps(tags, sort_keys=True),
                    is_official=True,
                )
                sess.add(event)
                sess.flush()
                stored_count += 1
                for instrument, confidence in matches[:5]:
                    sess.add(
                        BetaFilingEventLink(
                            event_id=event.id,
                            instrument_id=instrument.id,
                            symbol=instrument.symbol,
                            linkage_method="SYMBOL_OR_NAME",
                            confidence_score=confidence,
                        )
                    )
                    linked_count += 1

            run.stored_count = stored_count
            run.linked_count = linked_count

        return {
            "status": "SUCCESS" if error_text is None else "ERROR",
            "fetched_count": fetched_count,
            "stored_count": stored_count,
            "linked_count": linked_count,
            "error": error_text or "",
        }
