from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Mapping

from signal_score.constants import REASON_FUTURE_VISIT, REASON_MARKETING_FLAG


@dataclass(frozen=True)
class ScoreResult:
    flagged: bool
    score: int | None
    flag_reasons: tuple[str, ...] = ()
    website: int = 0
    marketing: int = 0
    intent: int = 0
    hiring: int = 0


def is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str) and value.strip() == "":
        return True
    return False


def is_true(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if is_blank(value):
        return False
    return str(value).strip().lower() == "true"


def parse_number(value: Any) -> float:
    if is_blank(value):
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def parse_hubspot_date(value: Any) -> date | None:
    """Parse a HubSpot date or datetime into a UTC calendar date."""
    if is_blank(value):
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)):
        return _date_from_epoch(value)
    text = str(value).strip()
    if text.isdigit():
        return _date_from_epoch(int(text))
    if "T" in text:
        iso = text.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(iso)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).date()
    return date.fromisoformat(text[:10])


def _date_from_epoch(raw: int | float) -> date:
    ts = float(raw)
    if ts > 1e12:
        ts = ts / 1000.0
    return datetime.fromtimestamp(ts, tz=timezone.utc).date()


def utc_today() -> date:
    return datetime.now(timezone.utc).date()


def website_activity_points(days_ago: int | None) -> int:
    if days_ago is None:
        return 0
    if days_ago <= 7:
        return 35
    if days_ago <= 28:
        return 18
    if days_ago <= 90:
        return 9
    return 0


def _multi_values(raw: Any) -> list[str]:
    """Split a HubSpot multi-checkbox (semicolon-separated string or list)."""
    if isinstance(raw, (list, tuple)):
        parts = [str(item).strip() for item in raw]
    elif is_blank(raw):
        return []
    else:
        parts = [part.strip() for part in str(raw).split(";")]
    return [part for part in parts if part]


def marketing_base_points(properties: Mapping[str, Any]) -> int:
    if is_true(properties.get("high_engagement_event_attendee")):
        return 30
    values = {item.lower() for item in _multi_values(properties.get("marketing_event_type"))}
    if "channel event attendee" in values:
        return 18
    if "general marketing event attendee" in values:
        return 10
    return 0


def intent_points(properties: Mapping[str, Any]) -> int:
    values = _multi_values(properties.get("competitor_intent"))
    if not values:
        return 0
    if any("cribl" in item.lower() for item in values):
        return 20
    return 10


def hiring_points(properties: Mapping[str, Any]) -> int:
    ciso = parse_number(properties.get("common_room_hiring_for_ciso"))
    soc_leaders = parse_number(properties.get("common_room_hiring_for_soc_leaders"))
    soc_team = parse_number(properties.get("common_room_hiring_for_soc_team"))
    if ciso > 0 or soc_leaders > 0:
        return 15
    if soc_team >= 10:
        return 10
    if 5 <= soc_team <= 9:
        return 6
    if 1 <= soc_team <= 4:
        return 3
    return 0


def compute_score(properties: Mapping[str, Any], as_of: date | None = None) -> ScoreResult:
    """Pure scoring / flag evaluation. No HubSpot I/O."""
    as_of = as_of or utc_today()
    reasons: list[str] = []

    visit = parse_hubspot_date(properties.get("last_web_visit_cr"))
    days_ago: int | None
    if visit is None:
        days_ago = None
    else:
        days_ago = (as_of - visit).days
        if days_ago < 0:
            reasons.append(REASON_FUTURE_VISIT)

    base_marketing = marketing_base_points(properties)
    distinct = parse_number(properties.get("distinct_marketing_events_attended"))
    if base_marketing == 0 and distinct >= 2:
        reasons.append(REASON_MARKETING_FLAG)

    if reasons:
        return ScoreResult(flagged=True, score=None, flag_reasons=tuple(reasons))

    website = website_activity_points(days_ago)
    marketing = base_marketing
    if base_marketing > 0 and distinct >= 2:
        marketing += 5
    intent = intent_points(properties)
    hiring = hiring_points(properties)
    score = min(100, website + marketing + intent + hiring)
    return ScoreResult(
        flagged=False,
        score=score,
        website=website,
        marketing=marketing,
        intent=intent,
        hiring=hiring,
    )
