"""Lightweight RSS/Atom news ingestion for beta research."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree

import httpx
from sqlalchemy import desc, func, select

from ..context import BetaContext
from ..db.models import (
    BetaInstrument,
    BetaNewsArticle,
    BetaNewsArticleLink,
    BetaNewsIngestionRun,
    BetaNewsSource,
)

_DEFAULT_SOURCES = (
    {
        "source_name": "BBC Business",
        "feed_url": "https://feeds.bbci.co.uk/news/business/rss.xml",
        "market": "UK",
    },
    {
        "source_name": "CNBC Top News",
        "feed_url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "market": "US",
    },
)

_POSITIVE_KEYWORDS = {
    "beats",
    "beat",
    "surge",
    "surges",
    "growth",
    "gains",
    "gain",
    "rises",
    "rise",
    "record",
    "strong",
    "upgrade",
    "profit",
    "profits",
    "expands",
    "expansion",
    "acquires",
    "acquisition",
}
_NEGATIVE_KEYWORDS = {
    "misses",
    "miss",
    "falls",
    "fall",
    "drops",
    "drop",
    "warning",
    "warns",
    "cut",
    "cuts",
    "downgrade",
    "loss",
    "losses",
    "lawsuit",
    "probe",
    "investigation",
    "decline",
    "slump",
    "weak",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        parsed = parsedate_to_datetime(raw)
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except (TypeError, ValueError):
        pass
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def _strip_text(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _sentiment_for(title: str, summary: str | None) -> tuple[str, float, list[str]]:
    text = f"{title} {summary or ''}".lower()
    positive_hits = sorted({word for word in _POSITIVE_KEYWORDS if word in text})
    negative_hits = sorted({word for word in _NEGATIVE_KEYWORDS if word in text})
    score = float(len(positive_hits) - len(negative_hits))
    if score > 0:
        label = "POSITIVE"
    elif score < 0:
        label = "NEGATIVE"
    else:
        label = "NEUTRAL"
    tags = positive_hits + negative_hits
    return label, score, tags


def _parse_feed(xml_text: str) -> list[dict[str, object]]:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError:
        return []

    items: list[dict[str, object]] = []
    channel_items = root.findall(".//channel/item")
    if channel_items:
        for item in channel_items:
            title = _strip_text(item.findtext("title"))
            if not title:
                continue
            guid = _strip_text(item.findtext("guid") or item.findtext("link") or title)
            items.append(
                {
                    "guid": guid,
                    "title": title,
                    "link": _strip_text(item.findtext("link")) or None,
                    "summary": _strip_text(item.findtext("description")) or None,
                    "published_at": _parse_datetime(item.findtext("pubDate")),
                }
            )
        return items

    namespaces = {"atom": "http://www.w3.org/2005/Atom"}
    for entry in root.findall(".//atom:entry", namespaces):
        title = _strip_text(entry.findtext("atom:title", namespaces=namespaces))
        if not title:
            continue
        link_node = entry.find("atom:link", namespaces)
        link_href = link_node.attrib.get("href") if link_node is not None else None
        guid = _strip_text(entry.findtext("atom:id", namespaces=namespaces) or link_href or title)
        items.append(
            {
                "guid": guid,
                "title": title,
                "link": _strip_text(link_href) or None,
                "summary": _strip_text(entry.findtext("atom:summary", namespaces=namespaces))
                or _strip_text(entry.findtext("atom:content", namespaces=namespaces))
                or None,
                "published_at": _parse_datetime(
                    entry.findtext("atom:updated", namespaces=namespaces)
                    or entry.findtext("atom:published", namespaces=namespaces)
                ),
            }
        )
    return items


def _instrument_match_candidates(title: str, summary: str | None, instruments: list[BetaInstrument]) -> list[tuple[BetaInstrument, float]]:
    text = f"{title} {summary or ''}"
    upper = text.upper()
    lowered = text.lower()
    matches: list[tuple[BetaInstrument, float]] = []
    for instrument in instruments:
        symbol = instrument.symbol.upper()
        name = instrument.name.lower()
        confidence = 0.0
        if re.search(rf"\b{re.escape(symbol)}\b", upper):
            confidence = max(confidence, 0.95)
        if name and len(name) >= 5 and name in lowered:
            confidence = max(confidence, 0.7)
        if confidence > 0:
            matches.append((instrument, confidence))
    return matches


class BetaNewsService:
    """Persist low-cost news evidence that can be linked back to symbols."""

    @staticmethod
    def ensure_default_sources() -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"added": 0}

        added = 0
        with BetaContext.write_session() as sess:
            for row in _DEFAULT_SOURCES:
                existing = sess.scalar(
                    select(BetaNewsSource).where(
                        BetaNewsSource.source_name == row["source_name"],
                        BetaNewsSource.feed_url == row["feed_url"],
                    )
                )
                if existing is None:
                    sess.add(
                        BetaNewsSource(
                            source_name=row["source_name"],
                            feed_url=row["feed_url"],
                            market=row["market"],
                            source_type="RSS",
                            is_active=True,
                        )
                    )
                    added += 1
        return {"added": added}

    @staticmethod
    def ingest_active_sources(*, source_limit: int = 2, max_articles_per_source: int = 20) -> dict[str, int]:
        if not BetaContext.is_initialized():
            return {"sources_processed": 0, "articles_stored": 0, "links_stored": 0}

        with BetaContext.read_session() as sess:
            sources = list(
                sess.scalars(
                    select(BetaNewsSource)
                    .where(BetaNewsSource.is_active.is_(True))
                    .order_by(BetaNewsSource.created_at.asc())
                    .limit(source_limit)
                ).all()
            )
        total_articles = 0
        total_links = 0
        processed = 0
        for source in sources:
            result = BetaNewsService.ingest_source(source.id, max_articles=max_articles_per_source)
            total_articles += int(result.get("stored_count", 0))
            total_links += int(result.get("linked_count", 0))
            processed += 1
        return {
            "sources_processed": processed,
            "articles_stored": total_articles,
            "links_stored": total_links,
        }

    @staticmethod
    def ingest_source(source_id: str, *, xml_text: str | None = None, max_articles: int = 20) -> dict[str, int | str]:
        if not BetaContext.is_initialized():
            return {"stored_count": 0, "linked_count": 0, "status": "SKIPPED"}

        with BetaContext.read_session() as sess:
            source = sess.scalar(select(BetaNewsSource).where(BetaNewsSource.id == source_id))
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
        fetched_count = min(len(parsed_items), max_articles)
        now = _utcnow()

        with BetaContext.write_session() as sess:
            run = BetaNewsIngestionRun(
                source_id=source.id,
                status="SUCCESS" if error_text is None else "ERROR",
                fetched_count=fetched_count,
                stored_count=0,
                linked_count=0,
                error_text=error_text,
            )
            sess.add(run)
            sess.flush()

            cutoff = now - timedelta(days=7)
            for item in parsed_items[:max_articles]:
                published_at = item["published_at"] if isinstance(item["published_at"], datetime) else None
                if published_at is not None and published_at < cutoff:
                    continue
                guid = str(item["guid"])
                existing = sess.scalar(
                    select(BetaNewsArticle).where(
                        BetaNewsArticle.source_id == source.id,
                        BetaNewsArticle.article_guid == guid,
                    )
                )
                if existing is not None:
                    continue
                title = str(item["title"])
                summary = item["summary"] if isinstance(item["summary"], str) else None
                sentiment_label, sentiment_score, tags = _sentiment_for(title, summary)
                matches = _instrument_match_candidates(title, summary, instruments)
                article = BetaNewsArticle(
                    source_id=source.id,
                    article_guid=guid,
                    title=title,
                    link_url=item["link"] if isinstance(item["link"], str) else None,
                    summary_text=summary,
                    published_at=published_at,
                    fetched_at=now,
                    sentiment_label=sentiment_label,
                    sentiment_score=sentiment_score,
                    relevance_score=min(1.0, 0.25 + (0.25 * len(matches)) + (0.1 * abs(sentiment_score))),
                    matched_symbols_json=json.dumps([instrument.symbol for instrument, _ in matches], sort_keys=True),
                    keyword_tags_json=json.dumps(tags, sort_keys=True),
                )
                sess.add(article)
                sess.flush()
                stored_count += 1
                for instrument, confidence in matches[:5]:
                    sess.add(
                        BetaNewsArticleLink(
                            article_id=article.id,
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
