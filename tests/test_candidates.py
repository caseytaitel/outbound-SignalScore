from __future__ import annotations

from signal_score.constants import (
    ACTION_BLANK_EXCLUDED,
    ACTION_BLANK_STALE,
    ACTION_SCORE,
    SCOPE_CANDIDATE,
    SCOPE_TARGET,
)
from signal_score.hubspot_client import Company
from signal_score.orchestrator import _is_noop_candidate_blank, plan_company
from signal_score.pull import (
    EXCLUDED_COMPANY_TYPES,
    classify_scope,
    fetch_candidates,
    not_target_account_filter_groups,
    qualifies_as_candidate,
    type_exclusion_filter,
)

from conftest import FakeHubSpotClient

SCORING_COMPANY = {"siem_detected": "Strong", "high_engagement_event_attendee": "true"}


def test_type_exclusion_filter_contains_exact_mssp_reseller_literal():
    assert "MSSP / Reseller" in EXCLUDED_COMPANY_TYPES
    filter_ = type_exclusion_filter()
    assert filter_["operator"] == "NOT_IN"
    assert "MSSP / Reseller" in filter_["values"]


def test_not_target_account_filter_groups_stays_within_api_limits():
    groups = not_target_account_filter_groups([{"propertyName": "x", "operator": "HAS_PROPERTY"}])
    assert len(groups) <= 5
    for group in groups:
        assert len(group["filters"]) <= 6


def test_qualifies_as_candidate_requires_allowed_type_and_a_signal():
    assert qualifies_as_candidate({"type": "PROSPECT", "competitor_intent": "Cribl"})
    assert qualifies_as_candidate({"competitor_intent": "Cribl"})  # blank type is in scope
    assert not qualifies_as_candidate({"type": "CUSTOMER", "competitor_intent": "Cribl"})
    assert not qualifies_as_candidate({"type": "PROSPECT"})  # no signal present


def test_qualifies_as_candidate_ignores_zero_and_false_signal_values():
    assert not qualifies_as_candidate({"common_room_hiring_for_ciso": "0"})
    assert not qualifies_as_candidate({"high_engagement_event_attendee": "false"})
    assert qualifies_as_candidate({"common_room_hiring_for_ciso": "1"})


def test_classify_scope():
    target = {"hs_is_target_account": "true"}
    candidate = {"type": "PROSPECT", "competitor_intent": "Cribl"}

    assert classify_scope(target) == SCOPE_TARGET
    assert classify_scope(candidate) == SCOPE_CANDIDATE
    assert classify_scope({"type": "CUSTOMER", "competitor_intent": "Cribl"}) == ""
    assert classify_scope({"type": "PROSPECT"}) == ""  # allowed type, but no signal
    assert classify_scope({}) == ""


def test_fetch_candidates_dedupes_by_id():
    company_a = Company(id="1", properties={"name": "A"})
    company_b = Company(id="2", properties={"name": "B"})
    client = FakeHubSpotClient(responses_by_call=[[company_a], [company_a, company_b]])

    companies = fetch_candidates(client)  # type: ignore[arg-type]

    assert {company.id for company in companies} == {"1", "2"}
    assert len(companies) == 2


def test_plan_company_scores_candidate():
    company = Company(id="1", properties={"name": "A", "competitor_intent": "Cribl", **SCORING_COMPANY})

    item = plan_company(company)
    assert item.op.action == ACTION_SCORE
    assert item.scope == SCOPE_CANDIDATE


def test_plan_company_target_beats_candidate():
    company = Company(
        id="1",
        properties={
            "name": "A",
            "hs_is_target_account": "true",
            "competitor_intent": "Cribl",  # would also qualify it as a candidate
            **SCORING_COMPANY,
        },
    )

    item = plan_company(company)
    assert item.op.action == ACTION_SCORE
    assert item.scope == SCOPE_TARGET


def test_plan_company_blanks_company_that_is_neither_target_nor_candidate():
    company = Company(id="1", properties={"name": "A", "type": "CUSTOMER", **SCORING_COMPANY})

    item = plan_company(company)
    assert item.op.action == ACTION_BLANK_STALE
    assert item.scope == ""


def test_stale_shaped_company_that_qualifies_as_candidate_is_scored_not_blanked():
    """fetch_stale surfaces scored non-targets; plan_company re-scores the ones still in scope."""
    company = Company(
        id="1",
        properties={
            "name": "A",
            "signal_score": "40",
            "competitor_intent": "Cribl",
            **SCORING_COMPANY,
        },
    )

    item = plan_company(company)
    assert item.op.action == ACTION_SCORE
    assert item.scope == SCOPE_CANDIDATE


def test_noop_candidate_blank_is_skipped_but_target_blank_is_not():
    props = {"name": "A", "competitor_intent": "Cribl"}  # siem blank -> excluded -> blank write
    candidate = Company(id="1", properties=props)
    item = plan_company(candidate)
    assert item.op.action == ACTION_BLANK_EXCLUDED
    assert _is_noop_candidate_blank(item, candidate)

    already_scored = Company(id="1", properties={**props, "signal_score": "40"})
    assert not _is_noop_candidate_blank(plan_company(already_scored), already_scored)

    target = Company(id="2", properties={"name": "B", "hs_is_target_account": "true"})
    assert not _is_noop_candidate_blank(plan_company(target), target)
