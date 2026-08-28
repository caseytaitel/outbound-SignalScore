from __future__ import annotations

from pathlib import Path

from signal_score.constants import (
    ACTION_BLANK_EXCLUDED,
    ACTION_BLANK_FLAGGED,
    ACTION_BLANK_STALE,
    ACTION_SCORE,
    REASON_FUTURE_VISIT,
    REASON_OPEN_DEALS,
    REASON_STALE,
)
from signal_score.hubspot_client import Company
from signal_score.orchestrator import plan_company, read_flagged_ids, write_flagged_file
from signal_score.scoring import utc_today


def company(id: str, name: str, **props) -> Company:
    return Company(id=id, properties={"name": name, **props})


def test_plan_company_not_target_is_stale():
    item = plan_company(company("1", "Old", hs_is_target_account="false", signal_score="40"))
    assert item.op.action == ACTION_BLANK_STALE
    assert item.op.reason == REASON_STALE
    assert item.flagged is False
    assert item.op.signal_score == ""


def test_plan_company_excluded():
    item = plan_company(
        company("1", "Deal Co", hs_is_target_account="true", hs_num_open_deals=2, siem_detected="Strong")
    )
    assert item.op.action == ACTION_BLANK_EXCLUDED
    assert item.op.reason == REASON_OPEN_DEALS
    assert item.flagged is False


def test_plan_company_flagged():
    item = plan_company(
        company(
            "1",
            "Future Visit",
            hs_is_target_account="true",
            siem_detected="Strong",
            last_web_visit_cr="2099-01-01",
        )
    )
    assert item.flagged is True
    assert item.op.action == ACTION_BLANK_FLAGGED
    assert item.op.reason == REASON_FUTURE_VISIT


def test_plan_company_scored():
    item = plan_company(
        company(
            "1",
            "Hot",
            hs_is_target_account="true",
            siem_detected="Strong",
            last_web_visit_cr=utc_today().isoformat(),
        )
    )
    assert item.op.action == ACTION_SCORE
    assert item.op.signal_score == "35"
    assert item.flagged is False


def test_flagged_file_roundtrip(tmp_path: Path):
    flagged = plan_company(
        company(
            "99",
            "Flag Co",
            hs_is_target_account="true",
            siem_detected="Strong",
            last_web_visit_cr="2099-01-01",
        )
    )
    scored = plan_company(
        company(
            "1",
            "Hot",
            hs_is_target_account="true",
            siem_detected="Strong",
            last_web_visit_cr=utc_today().isoformat(),
        )
    )
    path = write_flagged_file(tmp_path, [scored, flagged])
    assert path.name.startswith("flagged_")
    assert read_flagged_ids(path) == ["99"]


def test_read_flagged_ids_skips_comments_and_blank(tmp_path: Path):
    path = tmp_path / "flagged.txt"
    path.write_text("# header\n\n111\tAcme\tfoo\n222 extra\n", encoding="utf-8")
    assert read_flagged_ids(path) == ["111", "222"]
