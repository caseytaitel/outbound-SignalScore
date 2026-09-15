from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from signal_score.constants import (
    REASON_INTRO_DEMO,
    REASON_OPEN_DEALS,
    REASON_SIEM,
    SCOPE_CANDIDATE,
    SCOPE_TARGET,
    SIGNAL_SCORE_PROPERTY,
    UNIVERSE_PROPERTIES,
)
from signal_score.hubspot_client import Company, HubSpotClient
from signal_score.scoring import is_blank, is_true, parse_number

# Company `type` values that disqualify a company from being a candidate. Everything
# else -- including PROSPECT and blank/unset `type` -- is in scope. Blank is deliberate:
# nobody marked those non-prospect, so it's more likely an oversight than an exclusion.
EXCLUDED_COMPANY_TYPES: frozenset[str] = frozenset(
    {
        "CUSTOMER",
        "RESELLER",
        "MSSP",
        "MSSP / Reseller",
        "PARTNER",
        "VENDOR",
        "INVESTOR",
        "OTHER",
    }
)

# Coarse "this signal is present at all" conditions, used both as HubSpot filters and
# (via signal_is_present) as the Python-side scope check. Deliberately looser than the
# scoring gates in scoring.py: these only bound the pull, compute_score decides the score.
SIGNAL_CONDITIONS: list[tuple[str, dict[str, Any]]] = [
    ("web_visit", {"propertyName": "last_web_visit_cr", "operator": "HAS_PROPERTY"}),
    ("marketing_event_type", {"propertyName": "marketing_event_type", "operator": "HAS_PROPERTY"}),
    ("competitor_intent", {"propertyName": "competitor_intent", "operator": "HAS_PROPERTY"}),
    ("hiring_ciso", {"propertyName": "common_room_hiring_for_ciso", "operator": "GT", "value": "0"}),
    (
        "hiring_soc_leaders",
        {"propertyName": "common_room_hiring_for_soc_leaders", "operator": "GT", "value": "0"},
    ),
    (
        "hiring_soc_team",
        {"propertyName": "common_room_hiring_for_soc_team", "operator": "GT", "value": "0"},
    ),
    (
        "high_engagement",
        {"propertyName": "high_engagement_event_attendee", "operator": "EQ", "value": "true"},
    ),
    (
        "distinct_marketing_events",
        {"propertyName": "distinct_marketing_events_attended", "operator": "GT", "value": "0"},
    ),
]


@dataclass(frozen=True)
class ExclusionResult:
    excluded: bool
    reason: str | None


def signal_is_present(properties: Mapping[str, Any], condition_filter: Mapping[str, Any]) -> bool:
    """Python-side equivalent of one SIGNAL_CONDITIONS HubSpot filter."""
    value = properties.get(condition_filter["propertyName"])
    operator = condition_filter["operator"]
    if operator == "HAS_PROPERTY":
        return not is_blank(value)
    if operator == "GT":
        return parse_number(value) > parse_number(condition_filter["value"])
    if operator == "EQ":
        return not is_blank(value) and str(value).strip().lower() == condition_filter["value"]
    raise ValueError(f"unsupported signal condition operator: {operator}")


def qualifies_as_candidate(properties: Mapping[str, Any]) -> bool:
    """Pure. A candidate is any non-excluded `type` with at least one signal present."""
    company_type = str(properties.get("type") or "").strip()
    if company_type in EXCLUDED_COMPANY_TYPES:
        return False
    return any(signal_is_present(properties, filter_) for _name, filter_ in SIGNAL_CONDITIONS)


def classify_scope(properties: Mapping[str, Any]) -> str:
    """SCOPE_TARGET, SCOPE_CANDIDATE, or "" when out of scope (i.e. stale).

    Target wins over candidate: a target account that would also qualify as a candidate is
    always SCOPE_TARGET, so it keeps the full-refresh write behavior.

    Pure, so the isolated --company-id / --rescore-flagged paths classify a company the
    same way the full run does, without needing to know which query surfaced it.
    """
    if is_true(properties.get("hs_is_target_account")):
        return SCOPE_TARGET
    if qualifies_as_candidate(properties):
        return SCOPE_CANDIDATE
    return ""


def classify_exclusion(properties: Mapping[str, Any]) -> ExclusionResult:
    """Step 2 exclusion. Pure; no HubSpot I/O. First matching reason wins."""
    if parse_number(properties.get("hs_num_open_deals")) > 0:
        return ExclusionResult(excluded=True, reason=REASON_OPEN_DEALS)
    if not is_blank(properties.get("intro_demo_complete_date")):
        return ExclusionResult(excluded=True, reason=REASON_INTRO_DEMO)
    siem = properties.get("siem_detected")
    if is_blank(siem) or str(siem).strip().lower() == "not detected":
        return ExclusionResult(excluded=True, reason=REASON_SIEM)
    return ExclusionResult(excluded=False, reason=None)


def fetch_target_accounts(client: HubSpotClient) -> list[Company]:
    return client.search_companies(
        filter_groups=[
            {
                "filters": [
                    {
                        "propertyName": "hs_is_target_account",
                        "operator": "EQ",
                        "value": "true",
                    }
                ]
            }
        ],
        properties=UNIVERSE_PROPERTIES,
    )


def type_exclusion_filter() -> dict[str, Any]:
    return {"propertyName": "type", "operator": "NOT_IN", "values": sorted(EXCLUDED_COMPANY_TYPES)}


def not_target_account_filter_groups(base_filters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """hs_is_target_account != true as two OR'd groups (explicitly false, or unset)."""
    return [
        {
            "filters": [
                *base_filters,
                {"propertyName": "hs_is_target_account", "operator": "EQ", "value": "false"},
            ]
        },
        {
            "filters": [
                *base_filters,
                {"propertyName": "hs_is_target_account", "operator": "NOT_HAS_PROPERTY"},
            ]
        },
    ]


def fetch_candidates(client: HubSpotClient) -> list[Company]:
    """Non-target companies with an allowed `type` and at least one signal present.

    One search per signal condition (HubSpot caps filter groups per request), merged
    and de-duped by company id.
    """
    by_id: dict[str, Company] = {}
    for _name, condition_filter in SIGNAL_CONDITIONS:
        base_filters = [condition_filter, type_exclusion_filter()]
        for company in client.search_companies(
            filter_groups=not_target_account_filter_groups(base_filters),
            properties=UNIVERSE_PROPERTIES,
        ):
            by_id.setdefault(company.id, company)
    return list(by_id.values())


def fetch_universe(client: HubSpotClient) -> list[Company]:
    """The full universe: target accounts plus qualifying candidates, de-duped by id."""
    universe = fetch_target_accounts(client)
    target_ids = {company.id for company in universe}
    universe.extend(
        company for company in fetch_candidates(client) if company.id not in target_ids
    )
    return universe


def fetch_stale(client: HubSpotClient) -> list[Company]:
    """Companies with a populated signal_score whose target-account flag is not TRUE.

    Deliberately candidate-unaware, so this over-pulls: plan_company re-classifies every hit,
    and one that still qualifies as a candidate is re-scored rather than blanked.
    """
    return client.search_companies(
        filter_groups=[
            {
                "filters": [
                    {
                        "propertyName": SIGNAL_SCORE_PROPERTY,
                        "operator": "HAS_PROPERTY",
                    },
                    {
                        "propertyName": "hs_is_target_account",
                        "operator": "EQ",
                        "value": "false",
                    },
                ]
            },
            {
                "filters": [
                    {
                        "propertyName": SIGNAL_SCORE_PROPERTY,
                        "operator": "HAS_PROPERTY",
                    },
                    {
                        "propertyName": "hs_is_target_account",
                        "operator": "NOT_HAS_PROPERTY",
                    },
                ]
            },
        ],
        properties=UNIVERSE_PROPERTIES,
    )
