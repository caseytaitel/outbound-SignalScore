from __future__ import annotations

from pathlib import Path

from signal_score.constants import (
    ACTION_BLANK_EXCLUDED,
    ACTION_BLANK_STALE,
    ACTION_SCORE,
    HUBSPOT_APP_URL,
    SCOPE_CANDIDATE,
    SCOPE_TARGET,
)
from signal_score.notify import toast_spec
from signal_score.orchestrator import PlannedWrite, _updated_by_scope
from signal_score.writeback import WriteOp


def _spec(tmp_path: Path, **kwargs):
    defaults = {
        "write_failures": 0,
        "flagged": 0,
        "aborted": False,
        "logs_dir": tmp_path,
        "hubspot_app_url": HUBSPOT_APP_URL,
    }
    defaults.update(kwargs)
    return toast_spec(**defaults)


def test_success_toast_includes_target_and_candidate_counts(tmp_path):
    body, launch, label = _spec(tmp_path, targets=986, candidates=2388)

    assert body == "Signal Score: Complete — 986 targets, 2388 candidates updated."
    assert launch == HUBSPOT_APP_URL
    assert label == "Open HubSpot"


def test_success_toast_pluralizes_one_of_each(tmp_path):
    body, _, _ = _spec(tmp_path, targets=1, candidates=1)
    assert body == "Signal Score: Complete — 1 target, 1 candidate updated."


def test_success_toast_with_zero_candidates(tmp_path):
    body, _, _ = _spec(tmp_path, targets=986, candidates=0)
    assert body == "Signal Score: Complete — 986 targets, 0 candidates updated."


def test_write_failure_toast_unchanged(tmp_path):
    body, launch, label = _spec(tmp_path, write_failures=1)
    assert body == "Signal Score: 1 account failed to write."
    assert launch == tmp_path.resolve().as_uri()
    assert label == "Open logs"

    body, _, _ = _spec(tmp_path, write_failures=2)
    assert body == "Signal Score: 2 accounts failed to write."


def test_flagged_toast_unchanged(tmp_path):
    body, launch, label = _spec(tmp_path, flagged=1)
    assert body == "Signal Score: 1 account flagged for review."
    assert launch == tmp_path.resolve().as_uri()
    assert label == "Open logs"

    body, _, _ = _spec(tmp_path, flagged=3)
    assert body == "Signal Score: 3 accounts flagged for review."


def test_failed_and_flagged_toast_unchanged(tmp_path):
    body, _, _ = _spec(tmp_path, write_failures=2, flagged=4)
    assert body == "Signal Score: 2 failed, 4 flagged."


def test_aborted_toast_unchanged_and_ignores_counts(tmp_path):
    body, launch, label = _spec(tmp_path, aborted=True, targets=986, candidates=2388)
    assert body == "Signal Score: Run aborted — see logs."
    assert launch == tmp_path.resolve().as_uri()
    assert label == "Open logs"


def _item(scope: str, action: str) -> PlannedWrite:
    return PlannedWrite(
        op=WriteOp(
            company_id="1",
            name="A",
            action=action,
            signal_score="",
            reason="test",
        ),
        scope=scope,
    )


def test_updated_by_scope_counts_all_in_scope_writes_not_just_scored():
    planned = [
        _item(SCOPE_TARGET, ACTION_SCORE),
        _item(SCOPE_TARGET, ACTION_BLANK_EXCLUDED),
        _item(SCOPE_CANDIDATE, ACTION_SCORE),
        _item("", ACTION_BLANK_STALE),
    ]
    assert _updated_by_scope(planned) == (2, 1)
