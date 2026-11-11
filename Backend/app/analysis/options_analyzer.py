"""
GOLD BOT - Session Intelligence

Analyzes intraday trading sessions for gold.

Features:
- Asian, London and New York sessions
- Session overlaps
- Session high / low / range
- Opening range from configured session start
- Session return
- Session volume
- Average candle range
- Historical session activity comparison

IMPORTANT:
- All session windows use UTC.
- Default windows are approximations.
- Daylight-saving changes are not automatically applied.
- Input volume is broker-provided tick or real volume.
- This module does not execute trades.
"""

from collections import defaultdict
from collections.abc import Mapping, Sequence
from datetime import date, datetime, time, timedelta, timezone
from math import isfinite
from typing import Any


SUPPORTED_SYMBOL = "XAUUSD"

MINIMUM_OBSERVATIONS = 2
MAXIMUM_OBSERVATIONS = 100000

DEFAULT_OPENING_RANGE_MINUTES = 60

DEFAULT_SESSION_WINDOWS = (
    {
        "name": "ASIAN",
        "start": "00:00",
        "end": "08:00",
    },
    {
        "name": "LONDON",
        "start": "07:00",
        "end": "16:00",
    },
    {
        "name": "NEW_YORK",
        "start": "13:00",
        "end": "22:00",
    },
)


class SessionAnalysisError(ValueError):
    """Raised when session analysis input is invalid."""


def _number(value: Any, field: str) -> float:
    if isinstance(value, bool):
        raise SessionAnalysisError(
            f"{field} must be numeric."
        )

    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise SessionAnalysisError(
            f"{field} must be numeric."
        ) from exc

    if not isfinite(result):
        raise SessionAnalysisError(
            f"{field} must be finite."
        )

    return result


def _parse_timestamp(
    value: Any,
    field: str,
) -> datetime:

    if not isinstance(value, str):
        raise SessionAnalysisError(
            f"{field} must be an ISO-8601 timestamp."
        )

    normalized = (
        value[:-1] + "+00:00"
        if value.endswith("Z")
        else value
    )

    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError as exc:
        raise SessionAnalysisError(
            f"{field} is invalid."
        ) from exc

    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise SessionAnalysisError(
            f"{field} must include a timezone."
        )

    return parsed.astimezone(timezone.utc)


def _parse_clock(
    value: Any,
    field: str,
) -> time:

    if not isinstance(value, str):
        raise SessionAnalysisError(
            f"{field} must use HH:MM format."
        )

    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise SessionAnalysisError(
            f"{field} must use HH:MM format."
        ) from exc

    if (
        parsed.second != 0
        or parsed.microsecond != 0
        or parsed.tzinfo is not None
    ):
        raise SessionAnalysisError(
            f"{field} must use HH:MM format."
        )

    return parsed


def _validate_windows(
    session_windows: Sequence[Mapping[str, Any]] | None,
) -> list[dict[str, Any]]:

    if session_windows is None:
        session_windows = DEFAULT_SESSION_WINDOWS

    if (
        isinstance(session_windows, (str, bytes))
        or not isinstance(session_windows, Sequence)
        or not session_windows
    ):
        raise SessionAnalysisError(
            "session_windows must be a non-empty sequence."
        )

    validated = []
    names = set()

    for index, window in enumerate(session_windows):
        if not isinstance(window, Mapping):
            raise SessionAnalysisError(
                f"Session window {index} must be an object."
            )

        name = window.get("name")

        if not isinstance(name, str) or not name.strip():
            raise SessionAnalysisError(
                f"Session window {index} requires a name."
            )

        normalized_name = name.strip().upper()

        if normalized_name in names:
            raise SessionAnalysisError(
                f"Duplicate session name: {normalized_name}."
            )

        names.add(normalized_name)

        start = _parse_clock(
            window.get("start"),
            f"session_windows[{index}].start",
        )

        end = _parse_clock(
            window.get("end"),
            f"session_windows[{index}].end",
        )

        if start == end:
            raise SessionAnalysisError(
                f"Session {normalized_name} cannot have identical start and end."
            )

        validated.append({
            "name": normalized_name,
            "start": start,
            "end": end,
        })

    return validated


def _validate_observations(
    observations: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:

    if (
        isinstance(observations, (str, bytes))
        or not isinstance(observations, Sequence)
    ):
        raise SessionAnalysisError(
            "Observations must be a sequence."
        )

    if not (
        MINIMUM_OBSERVATIONS
        <= len(observations)
        <= MAXIMUM_OBSERVATIONS
    ):
        raise SessionAnalysisError(
            f"Observations must contain between "
            f"{MINIMUM_OBSERVATIONS} and "
            f"{MAXIMUM_OBSERVATIONS} candles."
        )

    validated = []
    previous_timestamp = None

    now = datetime.now(timezone.utc)

    for index, observation in enumerate(observations):
        if not isinstance(observation, Mapping):
            raise SessionAnalysisError(
                f"Invalid observation at index {index}."
            )

        timestamp = _parse_timestamp(
            observation.get("timestamp"),
            f"observations[{index}].timestamp",
        )

        if timestamp > now + timedelta(minutes=5):
            raise SessionAnalysisError(
                f"Future timestamp at index {index}."
            )

        open_price = _number(
            observation.get("open"),
            f"observations[{index}].open",
        )

        high = _number(
            observation.get("high"),
            f"observations[{index}].high",
        )

        low = _number(
            observation.get("low"),
            f"observations[{index}].low",
        )

        close = _number(
            observation.get("close"),
            f"observations[{index}].close",
        )

        volume = _number(
            observation.get("volume", 0),
            f"observations[{index}].volume",
        )

        if min(
            open_price,
            high,
            low,
            close,
        ) <= 0:
            raise SessionAnalysisError(
                f"Prices must be positive at index {index}."
            )

        if high < low:
            raise SessionAnalysisError(
                f"Invalid high/low range at index {index}."
            )

        if not low <= open_price <= high:
            raise SessionAnalysisError(
                f"Open is outside the candle range at index {index}."
            )

        if not low <= close <= high:
            raise SessionAnalysisError(
                f"Close is outside the candle range at index {index}."
            )

        if volume < 0:
            raise SessionAnalysisError(
                f"Volume cannot be negative at index {index}."
            )

        if (
            previous_timestamp is not None
            and timestamp <= previous_timestamp
        ):
            raise SessionAnalysisError(
                "Timestamps must be strictly increasing and unique."
            )

        previous_timestamp = timestamp

        validated.append({
            "timestamp": timestamp,
            "open": open_price,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        })

    return validated


def _session_start_datetime(
    timestamp: datetime,
    start: time,
    end: time,
) -> datetime | None:

    current_clock = timestamp.time().replace(tzinfo=None)
    session_date = timestamp.date()

    if start < end:
        if not start <= current_clock < end:
            return None
    else:
        # Overnight session, for example 22:00 -> 06:00.
        if current_clock >= start:
            pass
        elif current_clock < end:
            session_date -= timedelta(days=1)
        else:
            return None

    return datetime.combine(
        session_date,
        start,
        tzinfo=timezone.utc,
    )


def _assign_sessions(
    observations: Sequence[Mapping[str, Any]],
    windows: Sequence[Mapping[str, Any]],
) -> dict[tuple[str, date], list[dict[str, Any]]]:

    grouped = defaultdict(list)

    for candle in observations:
        timestamp = candle["timestamp"]

        for window in windows:
            session_start = _session_start_datetime(
                timestamp,
                window["start"],
                window["end"],
            )

            if session_start is None:
                continue

            key = (
                window["name"],
                session_start.date(),
            )

            grouped[key].append({
                **candle,
                "session_start": session_start,
            })

    return grouped


def _analyze_session_group(
    name: str,
    session_date: date,
    candles: Sequence[Mapping[str, Any]],
    opening_range_minutes: int,
) -> dict[str, Any]:

    ordered = sorted(
        candles,
        key=lambda item: item["timestamp"],
    )

    first = ordered[0]
    last = ordered[-1]

    session_high = max(
        candle["high"]
        for candle in ordered
    )

    session_low = min(
        candle["low"]
        for candle in ordered
    )

    session_range = session_high - session_low

    total_volume = sum(
        candle["volume"]
        for candle in ordered
    )

    candle_ranges = [
        candle["high"] - candle["low"]
        for candle in ordered
    ]

    average_candle_range = (
        sum(candle_ranges) / len(candle_ranges)
    )

    session_return = (
        (last["close"] - first["open"])
        / first["open"]
    ) * 100.0

    session_start = first["session_start"]

    opening_cutoff = (
        session_start
        + timedelta(minutes=opening_range_minutes)
    )

    opening_candles = [
        candle
        for candle in ordered
        if (
            session_start <= candle["timestamp"]
            < opening_cutoff
        )
    ]

    if opening_candles:
        opening_high = max(
            candle["high"]
            for candle in opening_candles
        )

        opening_low = min(
            candle["low"]
            for candle in opening_candles
        )
    else:
        opening_high = None
        opening_low = None

    range_percent = (
        session_range / first["open"] * 100.0
    )

    duration_minutes = (
        last["timestamp"] - first["timestamp"]
    ).total_seconds() / 60.0

    return {
        "session": name,
        "session_date": session_date.isoformat(),
        "session_start_utc": session_start.isoformat(),
        "first_observation_utc": first["timestamp"].isoformat(),
        "last_observation_utc": last["timestamp"].isoformat(),
        "candles_count": len(ordered),
        "high": session_high,
        "low": session_low,
        "range": session_range,
        "range_percent": range_percent,
        "open": first["open"],
        "close": last["close"],
        "change_percent": session_return,
        "total_volume": total_volume,
        "average_candle_range": average_candle_range,
        "opening_range": {
            "minutes_from_configured_session_start": (
                opening_range_minutes
            ),
            "configured_start_utc": session_start.isoformat(),
            "cutoff_utc": opening_cutoff.isoformat(),
            "high": opening_high,
            "low": opening_low,
            "range": (
                opening_high - opening_low
                if opening_high is not None
                and opening_low is not None
                else None
            ),
            "observed_candles": len(opening_candles),
            "status": (
                "OBSERVED"
                if opening_candles
                else "UNAVAILABLE"
            ),
            "completeness": (
                "NOT_VERIFIED_WITHOUT_EXPECTED_CANDLE_INTERVAL"
            ),
        },
        "observed_duration_minutes": duration_minutes,
        "volume_per_candle": (
            total_volume / len(ordered)
        ),
    }


def _compare_sessions(
    session_results: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:

    grouped = defaultdict(list)

    for result in session_results:
        grouped[result["session"]].append(result)

    comparisons = {}

    for session_name, results in grouped.items():
        total_sessions = len(results)

        average_range = sum(
            item["range"]
            for item in results
        ) / total_sessions

        average_volume = sum(
            item["total_volume"]
            for item in results
        ) / total_sessions

        average_candle_range = sum(
            item["average_candle_range"]
            for item in results
        ) / total_sessions

        positive_sessions = sum(
            1 for item in results
            if item["change_percent"] > 0
        )

        negative_sessions = sum(
            1 for item in results
            if item["change_percent"] < 0
        )

        comparisons[session_name] = {
            "observations_count": total_sessions,
            "average_range": average_range,
            "average_volume": average_volume,
            "average_candle_range": average_candle_range,
            "positive_sessions": positive_sessions,
            "negative_sessions": negative_sessions,
            "flat_sessions": (
                total_sessions
                - positive_sessions
                - negative_sessions
            ),
            "positive_session_rate_percent": (
                positive_sessions / total_sessions * 100.0
            ),
        }

    ranked_by_range = sorted(
        comparisons.items(),
        key=lambda item: item[1]["average_range"],
        reverse=True,
    )

    ranked_by_volume = sorted(
        comparisons.items(),
        key=lambda item: item[1]["average_volume"],
        reverse=True,
    )

    return {
        "by_session": comparisons,
        "most_volatile_session": (
            ranked_by_range[0][0]
            if ranked_by_range
            else None
        ),
        "highest_average_volume_session": (
            ranked_by_volume[0][0]
            if ranked_by_volume
            else None
        ),
    }


def _detect_overlaps(
    grouped_sessions: Mapping[
        tuple[str, date],
        Sequence[Mapping[str, Any]],
    ],
) -> list[dict[str, Any]]:

    by_date = defaultdict(dict)

    for (name, session_date), candles in grouped_sessions.items():
        by_date[session_date][name] = candles

    overlaps = []

    for session_date, sessions in by_date.items():
        names = sorted(sessions)

        for first_index in range(len(names)):
            for second_index in range(
                first_index + 1,
                len(names),
            ):
                first_name = names[first_index]
                second_name = names[second_index]

                first_candles = sessions[first_name]
                second_candles = sessions[second_name]

                first_times = {
                    item["timestamp"]
                    for item in first_candles
                }

                second_times = {
                    item["timestamp"]
                    for item in second_candles
                }

                common_times = first_times.intersection(
                    second_times
                )

                if not common_times:
                    continue

                shared_volume = sum(
                    item["volume"]
                    for item in first_candles
                    if item["timestamp"] in common_times
                )

                overlaps.append({
                    "session_date": session_date.isoformat(),
                    "sessions": [
                        first_name,
                        second_name,
                    ],
                    "overlapping_observations": len(common_times),
                    "first_common_timestamp_utc": (
                        min(common_times).isoformat()
                    ),
                    "last_common_timestamp_utc": (
                        max(common_times).isoformat()
                    ),
                    "shared_observation_volume": shared_volume,
                    "interpretation": (
                        "Both configured session windows contain "
                        "the same observed candle timestamps. "
                        "Shared volume is counted once."
                    ),
                })

    return overlaps


def analyze_sessions(
    observations: Sequence[Mapping[str, Any]],
    session_windows: Sequence[Mapping[str, Any]] | None = None,
    opening_range_minutes: int = DEFAULT_OPENING_RANGE_MINUTES,
    market_name: str = "GOLD",
) -> dict[str, Any]:

    if (
        isinstance(opening_range_minutes, bool)
        or not isinstance(opening_range_minutes, int)
        or not 1 <= opening_range_minutes <= 240
    ):
        raise SessionAnalysisError(
            "opening_range_minutes must be between 1 and 240."
        )

    if (
        not isinstance(market_name, str)
        or not market_name.strip()
    ):
        raise SessionAnalysisError(
            "market_name must be a non-empty string."
        )

    validated_observations = _validate_observations(
        observations
    )

    windows = _validate_windows(
        session_windows
    )

    grouped = _assign_sessions(
        validated_observations,
        windows,
    )

    session_results = []

    for (session_name, session_date), candles in sorted(
        grouped.items(),
        key=lambda item: (
            item[0][1],
            item[0][0],
        ),
    ):
        session_results.append(
            _analyze_session_group(
                session_name,
                session_date,
                candles,
                opening_range_minutes,
            )
        )

    comparisons = _compare_sessions(
        session_results
    )

    overlaps = _detect_overlaps(
        grouped
    )

    assigned_timestamps = {
        candle["timestamp"]
        for candles in grouped.values()
        for candle in candles
    }

    return {
        "module": "session_intelligence",
        "symbol": SUPPORTED_SYMBOL,
        "market_name": market_name.upper().strip(),
        "data_available": True,
        "timezone": "UTC",
        "input_observations": len(validated_observations),
        "assigned_observations": len(assigned_timestamps),
        "unassigned_observations": (
            len(validated_observations)
            - len(assigned_timestamps)
        ),
        "opening_range_minutes": opening_range_minutes,
        "configured_sessions": [
            {
                "name": window["name"],
                "start_utc": window["start"].isoformat(),
                "end_utc": window["end"].isoformat(),
                "crosses_midnight": (
                    window["start"] > window["end"]
                ),
            }
            for window in windows
        ],
        "sessions": session_results,
        "historical_comparison": comparisons,
        "overlaps": overlaps,
        "trading_signal_allowed": False,
        "limitations": [
            "Default session windows are approximate UTC windows.",
            "Daylight-saving changes are not automatically applied.",
            "Session results depend on the configured windows.",
            "Opening range starts at the configured session opening time.",
            "Opening range completeness cannot be verified without the expected candle interval.",
            "Missing candles can make session measurements incomplete.",
            "Volume represents supplied broker or data-provider volume.",
            "Overlapping sessions may contain the same market observations.",
            "Shared overlap volume is counted once in overlap reporting.",
            "Historical averages are descriptive and do not guarantee future volatility.",
            "This module does not fetch market data or execute orders.",
        ],
    }
