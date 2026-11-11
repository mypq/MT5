"""
GOLD BOT - News Intelligence Analyzer

Analyzes externally supplied news articles and statements.

Features:

- Source reliability classification
- Publication age validation
- Duplicate detection
- Keyword-based topic classification
- Potential gold impact classification
- Conflicting headline detection

Important:
This is a rule-based first version.
It does not understand the full meaning of every article.
A future NLP layer must analyze full article content.

This module does not fetch news or execute trading orders.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from typing import Any

MAX_NEWS_AGE_HOURS = 48
MAX_FUTURE_MINUTES = 5

SUPPORTED_SOURCE_LEVELS = {
"HIGH",
"MEDIUM",
"LOW",
"UNKNOWN",
}

TOPIC_KEYWORDS = {
"FEDERAL_RESERVE": [
"federal reserve",
"fed",
"fomc",
"powell",
"interest rate",
"rate decision",
],
"INFLATION": [
"inflation",
"cpi",
"pce",
"consumer prices",
"producer prices",
],
"EMPLOYMENT": [
"nonfarm payrolls",
"payrolls",
"unemployment",
"employment",
"jobs report",
],
"US_DOLLAR": [
"dollar",
"dxy",
"us dollar",
],
"TREASURY_YIELDS": [
"treasury yield",
"bond yield",
"treasury bonds",
"10-year yield",
"2-year yield",
],
"GEOPOLITICAL_RISK": [
"war",
"military conflict",
"geopolitical",
"sanctions",
"invasion",
"ceasefire",
"escalation",
],
"GOLD_MARKET": [
"gold",
"bullion",
"precious metals",
"central bank gold",
],
}

HIGH_IMPACT_KEYWORDS = [
"emergency",
"unexpected",
"surprise",
"rate decision",
"fomc",
"war",
"invasion",
"sanctions",
"major escalation",
"intervention",
]

BEARISH_GOLD_KEYWORDS = [
"hawkish",
"rate hike",
"higher interest rates",
"stronger dollar",
"rising treasury yields",
"hot inflation",
]

BULLISH_GOLD_KEYWORDS = [
"dovish",
"rate cut",
"lower interest rates",
"weaker dollar",
"falling treasury yields",
"safe haven demand",
]

SOURCE_LEVELS = {
"reuters.com": "HIGH",
"bloomberg.com": "HIGH",
"apnews.com": "HIGH",
"federalreserve.gov": "HIGH",
"bls.gov": "HIGH",
"bea.gov": "HIGH",
"treasury.gov": "HIGH",
"cnbc.com": "MEDIUM",
"marketwatch.com": "MEDIUM",
}

class NewsAnalysisError(ValueError):
"""Raised when news data is invalid."""

def _parse_timestamp(value: Any) -> datetime:
if not isinstance(value, str):
raise NewsAnalysisError(
"published_at must be an ISO-8601 string."
)

try:
    parsed = datetime.fromisoformat(
        value.replace("Z", "+00:00")
    )
except ValueError:
    raise NewsAnalysisError(
        "Invalid publication timestamp."
    )

if parsed.tzinfo is None:
    raise NewsAnalysisError(
        "Publication timestamp must include a timezone."
    )

return parsed.astimezone(timezone.utc)

def _normalize_text(value: str) -> str:
return " ".join(value.lower().split())

def _classify_source(source: str) -> str:
normalized = source.lower().strip()

for domain, level in SOURCE_LEVELS.items():
    if normalized == domain or normalized.endswith("." + domain):
        return level

return "UNKNOWN"

def _find_topics(text: str) -> list[str]:
topics = []

for topic, keywords in TOPIC_KEYWORDS.items():
    if any(keyword in text for keyword in keywords):
        topics.append(topic)

return topics

def _classify_impact(text: str) -> dict[str, Any]:
bullish_matches = [
keyword
for keyword in BULLISH_GOLD_KEYWORDS
if keyword in text
]

bearish_matches = [
    keyword
    for keyword in BEARISH_GOLD_KEYWORDS
    if keyword in text
]

if bullish_matches and bearish_matches:
    direction = "MIXED"
elif bullish_matches:
    direction = "POTENTIALLY_BULLISH"
elif bearish_matches:
    direction = "POTENTIALLY_BEARISH"
else:
    direction = "UNDETERMINED"

high_impact_matches = [
    keyword
    for keyword in HIGH_IMPACT_KEYWORDS
    if keyword in text
]

if len(high_impact_matches) >= 2:
    impact_level = "HIGH"
elif high_impact_matches:
    impact_level = "POTENTIALLY_HIGH"
else:
    impact_level = "UNASSESSED"

return {
    "direction": direction,
    "impact_level": impact_level,
    "bullish_evidence": bullish_matches,
    "bearish_evidence": bearish_matches,
    "high_impact_evidence": high_impact_matches,
}

def _fingerprint(title: str, source: str) -> str:
normalized = _normalize_text(title)

content = f"{normalized}|{source.lower().strip()}"

return sha256(
    content.encode("utf-8")
).hexdigest()

def _validate_news(
articles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

if isinstance(articles, (str, bytes)):
    raise NewsAnalysisError(
        "Articles must be a sequence."
    )

validated = []

for index, article in enumerate(articles):
    if not isinstance(article, Mapping):
        raise NewsAnalysisError(
            f"Invalid article at index {index}."
        )

    title = article.get("title")
    source = article.get("source")
    content = article.get("content", "")

    if not isinstance(title, str) or not title.strip():
        raise NewsAnalysisError(
            "Every article requires a title."
        )

    if not isinstance(source, str) or not source.strip():
        raise NewsAnalysisError(
            "Every article requires a source."
        )

    if not isinstance(content, str):
        raise NewsAnalysisError(
            "Article content must be text."
        )

    published_at = _parse_timestamp(
        article.get("published_at")
    )

    validated.append({
        "title": title.strip(),
        "source": source.strip(),
        "content": content.strip(),
        "published_at": published_at,
        "fingerprint": _fingerprint(
            title,
            source,
        ),
    })

return validated

def analyze_news(
articles: Sequence[Mapping[str, Any]],
now: datetime | None = None,
) -> dict[str, Any]:

if now is None:
    current_time = datetime.now(timezone.utc)
else:
    if now.tzinfo is None:
        raise NewsAnalysisError(
            "now must include a timezone."
        )

    current_time = now.astimezone(timezone.utc)

data = _validate_news(articles)

seen_fingerprints = set()

analyzed = []

for article in data:
    if article["fingerprint"] in seen_fingerprints:
        continue

    seen_fingerprints.add(
        article["fingerprint"]
    )

    age_hours = (
        current_time - article["published_at"]
    ).total_seconds() / 3600.0

    if age_hours < -(MAX_FUTURE_MINUTES / 60):
        freshness = "FUTURE_TIMESTAMP"
    elif age_hours < 0:
        freshness = "RECENT_FUTURE_TIMESTAMP"
    elif age_hours > MAX_NEWS_AGE_HOURS:
        freshness = "STALE"
    else:
        freshness = "CURRENT"

    combined_text = _normalize_text(
        article["title"] + " " + article["content"]
    )

    source_level = _classify_source(
        article["source"]
    )

    topics = _find_topics(combined_text)

    impact = _classify_impact(
        combined_text
    )

    analyzed.append({
        "title": article["title"],
        "source": article["source"],
        "source_reliability": source_level,
        "published_at": article["published_at"].isoformat(),
        "age_hours": age_hours,
        "freshness": freshness,
        "topics": topics,
        "gold_impact": impact,
        "fingerprint": article["fingerprint"],
    })

analyzed.sort(
    key=lambda item: item["age_hours"]
)

current_news = [
    item for item in analyzed
    if item["freshness"] == "CURRENT"
]

high_reliability_news = [
    item for item in current_news
    if item["source_reliability"] == "HIGH"
]

directions = {
    item["gold_impact"]["direction"]
    for item in high_reliability_news
    if item["gold_impact"]["direction"] in {
        "POTENTIALLY_BULLISH",
        "POTENTIALLY_BEARISH",
    }
}

if len(directions) > 1:
    overall_context = "CONFLICTING_NEWS"
elif directions == {"POTENTIALLY_BULLISH"}:
    overall_context = "BULLISH_NEWS_CONTEXT"
elif directions == {"POTENTIALLY_BEARISH"}:
    overall_context = "BEARISH_NEWS_CONTEXT"
else:
    overall_context = "INSUFFICIENT_CLEAR_NEWS"

return {
    "module": "news_intelligence",
    "data_available": bool(analyzed),
    "received_article_count": len(data),
    "unique_article_count": len(analyzed),
    "current_article_count": len(current_news),
    "high_reliability_current_count": len(
        high_reliability_news
    ),
    "articles": analyzed,
    "overall_news_context": overall_context,
    "limitations": [
        "This version uses keyword matching, not full semantic understanding.",
        "Source classification does not prove article authenticity.",
        "A headline may omit important context or contain misleading language.",
        "News impact depends on expectations and market positioning.",
        "Conflicting reports require human or advanced NLP review.",
        "This module does not fetch news or execute trading orders.",
    ],
}
