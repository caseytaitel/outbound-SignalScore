from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from signal_score.constants import (
    REASON_INTRO_DEMO,
    REASON_OPEN_DEALS,
    REASON_SIEM,
    SIGNAL_SCORE_PROPERTY,
    STALE_PROPERTIES,
    UNIVERSE_PROPERTIES,
)
from signal_score.hubspot_client import Company, HubSpotClient
from signal_score.scoring import is_blank, parse_number


@dataclass(frozen=True)
class ExclusionResult:
    excluded: bool
    reason: str | None


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


def fetch_universe(client: HubSpotClient) -> list[Company]:
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


def fetch_stale(client: HubSpotClient) -> list[Company]:
    """Companies with a populated signal_score whose target-account flag is not TRUE."""
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
        properties=STALE_PROPERTIES,
    )


def partition_universe(companies: list[Company]) -> tuple[list[tuple[Company, str]], list[Company]]:
    """Returns (excluded_with_reason, candidates_for_scoring)."""
    excluded: list[tuple[Company, str]] = []
    candidates: list[Company] = []
    for company in companies:
        result = classify_exclusion(company.properties)
        if result.excluded:
            excluded.append((company, result.reason or REASON_SIEM))
        else:
            candidates.append(company)
    return excluded, candidates
