from __future__ import annotations

from signal_score.hubspot_client import Company
from signal_score.pull import SIGNAL_CONDITIONS, fetch_stale, fetch_universe

from conftest import FakeHubSpotClient


def test_fetch_universe_always_pulls_candidates():
    """The universe is targets + candidates on every run -- there is no targets-only mode."""
    target = Company(id="1", properties={"name": "T", "hs_is_target_account": "true"})
    candidate = Company(id="2", properties={"name": "C", "competitor_intent": "Cribl"})
    client = FakeHubSpotClient(responses_by_call=[[target], [candidate]])

    companies = fetch_universe(client)  # type: ignore[arg-type]

    assert {company.id for company in companies} == {"1", "2"}
    # one target search, then one search per signal condition
    assert len(client.calls) == 1 + len(SIGNAL_CONDITIONS)


def test_fetch_universe_keeps_the_target_row_when_a_company_appears_in_both():
    from_target_search = Company(id="1", properties={"name": "T", "hs_is_target_account": "true"})
    from_candidate_search = Company(id="1", properties={"name": "T", "competitor_intent": "Cribl"})
    client = FakeHubSpotClient(
        responses_by_call=[[from_target_search], [from_candidate_search]]
    )

    companies = fetch_universe(client)  # type: ignore[arg-type]

    assert len(companies) == 1
    assert companies[0] is from_target_search


def test_fetch_stale_query_shape_is_unchanged_by_candidates():
    """Deliberately candidate-unaware: it over-pulls and plan_company re-classifies the rows."""
    client = FakeHubSpotClient(responses_by_call=[[]])

    fetch_stale(client)  # type: ignore[arg-type]

    groups = client.calls[0]["filter_groups"]
    assert len(groups) == 2
    for group in groups:
        operators = {(f["propertyName"], f["operator"]) for f in group["filters"]}
        assert ("signal_score", "HAS_PROPERTY") in operators
    target_clauses = {
        (f["propertyName"], f["operator"])
        for group in groups
        for f in group["filters"]
        if f["propertyName"] == "hs_is_target_account"
    }
    assert target_clauses == {
        ("hs_is_target_account", "EQ"),
        ("hs_is_target_account", "NOT_HAS_PROPERTY"),
    }
