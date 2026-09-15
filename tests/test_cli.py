from __future__ import annotations

from pathlib import Path

import pytest

from signal_score.orchestrator import (
    TODAYS_FLAGGED_FILE,
    parse_args,
    resolve_flagged_path,
    utc_date_stamp,
)


def test_no_flags_is_a_full_live_run():
    args = parse_args([])

    assert args.dry_run is False
    assert args.company_ids is None
    assert args.write_blank is False
    assert args.rescore_flagged is None


def test_include_candidates_flag_is_gone():
    """Candidates are unconditional now; the flag must not quietly come back half-wired."""
    with pytest.raises(SystemExit) as exc:
        parse_args(["--include-candidates"])
    assert exc.value.code == 2


def test_flagged_file_flag_is_gone():
    with pytest.raises(SystemExit) as exc:
        parse_args(["--rescore-flagged", "--flagged-file", "logs/x.txt"])
    assert exc.value.code == 2


def test_rescore_flagged_bare_uses_the_sentinel():
    assert parse_args(["--rescore-flagged"]).rescore_flagged is TODAYS_FLAGGED_FILE


def test_rescore_flagged_with_path():
    args = parse_args(["--rescore-flagged", "logs/flagged_20260828.txt"])
    assert args.rescore_flagged == "logs/flagged_20260828.txt"


def test_rescore_flagged_equals_form():
    assert parse_args(["--rescore-flagged=logs/x.txt"]).rescore_flagged == "logs/x.txt"


def test_rescore_flagged_does_not_swallow_a_following_flag():
    """nargs="?" must not eat --dry-run as the PATH. README documents this exact invocation."""
    args = parse_args(["--rescore-flagged", "--dry-run"])

    assert args.rescore_flagged is TODAYS_FLAGGED_FILE
    assert args.dry_run is True


@pytest.mark.parametrize(
    "argv",
    [
        ["--write-blank"],
        ["--rescore-flagged", "--company-id", "1"],
        ["--rescore-flagged", "logs/x.txt", "--company-id", "1"],
        ["--rescore-flagged", "--write-blank"],
    ],
)
def test_rejected_flag_combinations(argv):
    with pytest.raises(SystemExit) as exc:
        parse_args(argv)
    assert exc.value.code == 2


def test_empty_rescore_flagged_path_is_still_a_rescore_run():
    """Regression: under a truthiness check "" would skip validation and go full live write."""
    with pytest.raises(SystemExit) as exc:
        parse_args(["--rescore-flagged", "", "--company-id", "1"])
    assert exc.value.code == 2


def test_resolve_flagged_path(tmp_path):
    assert resolve_flagged_path(TODAYS_FLAGGED_FILE, tmp_path) == (
        tmp_path / f"flagged_{utc_date_stamp()}.txt"
    )
    assert resolve_flagged_path("logs/flagged_20260828.txt", tmp_path) == Path(
        "logs/flagged_20260828.txt"
    )
