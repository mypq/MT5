"""
GOLD BOT - News Intelligence Analyzer

Features:
- Strict article input validation
- Trusted domain classification
- Publication timestamp and freshness validation
- Exact and near-duplicate headline detection
- Keyword-based topic classification
- Potential gold impact classification
- Conflicting news detection
- Evidence tracking and data quality reporting

Important:
This is a rule-based analyzer.
It does not provide full semantic understanding.
It does not fetch news or execute trading orders.
"""

from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from difflib import SequenceMatcher
from hashlib import sha256
from math import isfinite
from typing import Any
from urllib.parse import urlsplit


MAX_NEWS_AGE_HOURS = 48
MAX_FUTURE_MINUTES = 5
MAX_ARTICLES = 500
NEAR_DUPLICATE_THRESHOLD = 0.94

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


class NewsAnalysisError(ValueError):
    """Raised when news data is invalid."""


def _parse_timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        parsed = value

    elif isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(
                value.strip().replace("Z", "+00:00")
            )
        except ValueError as exc:
            raise NewsAnalysisError(
                "Invalid publication timestamp."
            ) from exc

    else:
        raise NewsAnalysisError(
            "published_at must be an ISO-8601 string or datetime."
        )

    if parsed.tzinfo is None:
        raise NewsAnalysisError(
            "Publication timestamp must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _normalize_text(value: str) -> str:
    return " ".join(value.casefold().split())


def _normalize_source(source: str) -> str:
    value = source.strip().casefold()

    if not value:
        return ""

    candidate = value

    if "://" not in candidate:
        candidate = "https://" + candidate

    try:
        parsed = urlsplit(candidate)
        hostname = parsed.hostname
    except ValueError:
        return ""

    if not hostname:
        return ""

    return hostname.rstrip(".")


def _classify_source(source: str) -> str:
    hostname = _normalize_source(source)

    if not hostname:
        return "UNKNOWN"

    for domain, level in SOURCE_LEVELS.items():
        if hostname == domain or hostname.endswith("." + domain):
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


def _fingerprint(title: str) -> str:
    normalized = _normalize_text(title)

    return sha256(
        normalized.encode("utf-8")
    ).hexdigest()


def _validate_news(
    articles: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    if not isinstance(articles, Sequence) or isinstance(
        articles, (str, bytes)
    ):
        raise NewsAnalysisError(
            "Articles must be a sequence."
        )

    if len(articles) > MAX_ARTICLES:
        raise NewsAnalysisError(
            f"Maximum supported article count is {MAX_ARTICLES}."
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
                f"Article {index} requires a title."
            )

        if not isinstance(source, str) or not source.strip():
            raise NewsAnalysisError(
                f"Article {index} requires a source."
            )

        if not isinstance(content, str):
            raise NewsAnalysisError(
                f"Article {index} content must be text."
            )

        published_at = _parse_timestamp(
            article.get("published_at")
        )

        validated.append({
            "title": title.strip(),
            "source": source.strip(),
            "content": content.strip(),
            "published_at": published_at,
            "fingerprint": _fingerprint(title),
        })

    return validated


def _is_near_duplicate(
    first_title: str,
    second_title: str,
) -> bool:
    first = _normalize_text(first_title)
    second = _normalize_text(second_title)

    if first == second:
        return True

    if not first or not second:
        return False

    similarity = SequenceMatcher(
        None,
        first,
        second,
        autojunk=False,
    ).ratio()

    return similarity >= NEAR_DUPLICATE_THRESHOLD


def _deduplicate_articles(
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    unique = []
    duplicates_removed = 0

    exact_fingerprints = set()
    recent_titles = []

    for article in articles:
        fingerprint = article["fingerprint"]

        if fingerprint in exact_fingerprints:
            duplicates_removed += 1
            continue

        title = article["title"]

        if any(
            _is_near_duplicate(title, previous_title)
            for previous_title in recent_titles
        ):
            duplicates_removed += 1
            continue

        exact_fingerprints.add(fingerprint)
        recent_titles.append(title)
        unique.append(article)

    return unique, duplicates_removed


def _classify_freshness(
    published_at: datetime,
    current_time: datetime,
) -> tuple[str, float]:
    age_hours = (
        current_time - published_at
    ).total_seconds() / 3600.0

    if age_hours < -(MAX_FUTURE_MINUTES / 60.0):
        return "FUTURE_TIMESTAMP", age_hours

    if age_hours < 0:
        return "RECENT_FUTURE_TIMESTAMP", age_hours

    if age_hours > MAX_NEWS_AGE_HOURS:
        return "STALE", age_hours

    return "CURRENT", age_hours


def _find_conflicting_news(
    articles: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    directional_articles = [
        article
        for article in articles
        if article["freshness"] == "CURRENT"
        and article["source_reliability"] == "HIGH"
        and article["gold_impact"]["direction"] in {
            "POTENTIALLY_BULLISH",
            "POTENTIALLY_BEARISH",
        }
    ]

    conflicts = []

    for index, first in enumerate(directional_articles):
        for second in directional_articles[index + 1:]:
            first_topics = set(first["topics"])
            second_topics = set(second["topics"])

            if not first_topics.intersection(second_topics):
                continue

            first_direction = first["gold_impact"]["direction"]
            second_direction = second["gold_impact"]["direction"]

            if first_direction != second_direction:
                conflicts.append({
                    "first_title": first["title"],
                    "second_title": second["title"],
                    "shared_topics": sorted(
                        first_topics.intersection(second_topics)
                    ),
                    "first_direction": first_direction,
                    "second_direction": second_direction,
                })

    return conflicts


def analyze_news(
    articles: Sequence[Mapping[str, Any]],
    now: datetime | None = None,
) -> dict[str, Any]:
    if now is None:
        current_time = datetime.now(timezone.utc)

    else:
        if not isinstance(now, datetime):
            raise NewsAnalysisError(
                "now must be a datetime object."
            )

        if now.tzinfo is None:
            raise NewsAnalysisError(
                "now must include a timezone."
            )

        current_time = now.astimezone(timezone.utc)

    data = _validate_news(articles)

    unique_articles, duplicates_removed = (
        _deduplicate_articles(data)
    )

    analyzed = []

    for article in unique_articles:
        freshness, age_hours = _classify_freshness(
            article["published_at"],
            current_time,
        )

        combined_text = _normalize_text(
            article["title"] + " " + article["content"]
        )

        source_level = _classify_source(
            article["source"]
        )

        topics = _find_topics(combined_text)

        impact = _classify_impact(combined_text)

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
        item
        for item in analyzed
        if item["freshness"] == "CURRENT"
    ]

    stale_news = [
        item
        for item in analyzed
        if item["freshness"] == "STALE"
    ]

    future_news = [
        item
        for item in analyzed
        if item["freshness"] in {
            "FUTURE_TIMESTAMP",
            "RECENT_FUTURE_TIMESTAMP",
        }
    ]

    high_reliability_news = [
        item
        for item in current_news
        if item["source_reliability"] == "HIGH"
    ]

    conflicts = _find_conflicting_news(analyzed)

    directions = {
        item["gold_impact"]["direction"]
        for item in high_reliability_news
        if item["gold_impact"]["direction"] in {
            "POTENTIALLY_BULLISH",
            "POTENTIALLY_BEARISH",
        }
    }

    if conflicts:
        overall_context = "CONFLICTING_NEWS"

    elif directions == {"POTENTIALLY_BULLISH"}:
        overall_context = "BULLISH_NEWS_CONTEXT"

    elif directions == {"POTENTIALLY_BEARISH"}:
        overall_context = "BEARISH_NEWS_CONTEXT"

    else:
        overall_context = "INSUFFICIENT_CLEAR_NEWS"

    topic_counts = {}

    for article in current_news:
        for topic in article["topics"]:
            topic_counts[topic] = (
                topic_counts.get(topic, 0) + 1
            )

    return {
        "module": "news_intelligence",
        "data_available": bool(analyzed),
        "received_article_count": len(data),
        "unique_article_count": len(analyzed),
        "duplicates_removed": duplicates_removed,
        "current_article_count": len(current_news),
        "stale_article_count": len(stale_news),
        "future_timestamp_article_count": len(future_news),
        "high_reliability_current_count": len(
            high_reliability_news
        ),
        "articles": analyzed,
        "topic_counts": topic_counts,
        "conflicting_news": conflicts,
        "conflicting_news_count": len(conflicts),
        "overall_news_context": overall_context,
        "data_quality": {
            "has_current_news": bool(current_news),
            "has_high_reliability_news": bool(
                high_reliability_news
            ),
            "has_stale_news": bool(stale_news),
            "has_future_timestamp_news": bool(future_news),
            "max_news_age_hours": MAX_NEWS_AGE_HOURS,
        },
        "limitations": [
            "This version uses keyword matching, not full semantic understanding.",
            "Source classification does not prove article authenticity.",
            "Only configured domains receive a known reliability level.",
            "Near-duplicate detection is approximate and title-based.",
            "Keyword matching may misunderstand negation or context.",
            "A headline may omit important information.",
            "News impact depends on expectations and market positioning.",
            "Conflicting reports require deeper semantic verification.",
            "News timestamps depend on the supplied provider.",
            "This module does not fetch live news.",
            "This module does not execute trading orders.",
        ],
        }
