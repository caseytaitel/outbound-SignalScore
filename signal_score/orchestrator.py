from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from signal_score.config import ConfigError, load_config
from signal_score.constants import (
    ACTION_BLANK_EXCLUDED,
    ACTION_BLANK_FLAGGED,
    ACTION_BLANK_STALE,
    ACTION_SCORE,
    REASON_SCORED,
    REASON_STALE,
)
from signal_score.hubspot_client import (
    HubSpotAuthError,
    HubSpotClient,
    HubSpotError,
    HubSpotPullError,
    HubSpotRateLimitError,
)
from signal_score.notify import show_run_toast
from signal_score.pull import fetch_stale, fetch_universe, partition_universe
from signal_score.scoring import compute_score
from signal_score.writeback import WriteOp, blank_value, score_value, write_ops

log = logging.getLogger("signal_score")


@dataclass
class PlannedWrite:
    op: WriteOp
    flagged: bool = False


class AbortRun(Exception):
    """Pull/auth/network failed before writes."""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Compute and write HubSpot Signal Scores.")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Pull, exclude, flag, and score, but do not write to HubSpot.",
    )
    args = parser.parse_args(argv)

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.logs_dir)
    dry_run = args.dry_run
    log.info("Signal Score run starting (dry_run=%s)", dry_run)

    try:
        client = HubSpotClient(config.token, base_url=config.hubspot_base_url)
        planned = plan_writes(client)
    except AbortRun as exc:
        log.error("Run aborted: %s", exc)
        print(f"Run aborted: {exc}", file=sys.stderr)
        if not dry_run:
            show_run_toast(
                write_failures=0,
                flagged=0,
                aborted=True,
                logs_dir=config.logs_dir,
                hubspot_app_url=config.hubspot_app_url,
            )
        return 1

    flagged_count = sum(1 for item in planned if item.flagged)
    _log_plan_summary(planned, flagged_count)

    if dry_run:
        csv_path = write_dry_run_csv(config.logs_dir, planned)
        log.info("Dry-run CSV written to %s", csv_path)
        print(f"Dry-run complete. Report: {csv_path}")
        _print_console_summary(planned, flagged_count, write_failures=0)
        return 0

    failures = write_ops(client, [item.op for item in planned])
    log.info(
        "Live run complete: %s writes planned, %s failed, %s flagged",
        len(planned),
        len(failures),
        flagged_count,
    )
    _print_console_summary(planned, flagged_count, write_failures=len(failures))
    show_run_toast(
        write_failures=len(failures),
        flagged=flagged_count,
        aborted=False,
        logs_dir=config.logs_dir,
        hubspot_app_url=config.hubspot_app_url,
    )
    return 0 if not failures else 1


def plan_writes(client: HubSpotClient) -> list[PlannedWrite]:
    try:
        universe = fetch_universe(client)
        stale = fetch_stale(client)
    except (HubSpotAuthError, HubSpotPullError, HubSpotRateLimitError, HubSpotError) as exc:
        raise AbortRun(str(exc)) from exc

    log.info("Universe pull: %s target accounts", len(universe))
    excluded, candidates = partition_universe(universe)
    log.info("Excluded: %s; scoring candidates: %s", len(excluded), len(candidates))

    planned: list[PlannedWrite] = []

    for company, reason in excluded:
        planned.append(
            PlannedWrite(
                op=WriteOp(
                    company_id=company.id,
                    name=company.name,
                    action=ACTION_BLANK_EXCLUDED,
                    signal_score=blank_value(),
                    reason=reason,
                )
            )
        )

    for company in candidates:
        result = compute_score(company.properties)
        if result.flagged:
            reason = ";".join(result.flag_reasons)
            log.warning("FLAGGED (%s): %s %s", reason, company.id, company.name)
            planned.append(
                PlannedWrite(
                    op=WriteOp(
                        company_id=company.id,
                        name=company.name,
                        action=ACTION_BLANK_FLAGGED,
                        signal_score=blank_value(),
                        reason=reason,
                    ),
                    flagged=True,
                )
            )
        else:
            assert result.score is not None
            planned.append(
                PlannedWrite(
                    op=WriteOp(
                        company_id=company.id,
                        name=company.name,
                        action=ACTION_SCORE,
                        signal_score=score_value(result.score),
                        reason=REASON_SCORED,
                    )
                )
            )

    universe_ids = {company.id for company in universe}
    stale_unique = [company for company in stale if company.id not in universe_ids]
    log.info("Stale scores to clear: %s", len(stale_unique))
    for company in stale_unique:
        planned.append(
            PlannedWrite(
                op=WriteOp(
                    company_id=company.id,
                    name=company.name,
                    action=ACTION_BLANK_STALE,
                    signal_score=blank_value(),
                    reason=REASON_STALE,
                )
            )
        )

    return planned


def write_dry_run_csv(logs_dir: Path, planned: list[PlannedWrite]) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    path = logs_dir / f"dry_run_{stamp}.csv"
    with path.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=["company_id", "name", "action", "signal_score", "reason"],
        )
        writer.writeheader()
        for item in planned:
            writer.writerow(
                {
                    "company_id": item.op.company_id,
                    "name": item.op.name,
                    "action": item.op.action,
                    "signal_score": item.op.signal_score,
                    "reason": item.op.reason,
                }
            )
    return path


def setup_logging(logs_dir: Path) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log_path = logs_dir / f"signal_score_{stamp}.log"
    formatter = logging.Formatter("%(asctime)s %(levelname)s %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(file_handler)
    root.addHandler(console_handler)
    log.info("Logging to %s", log_path)


def _log_plan_summary(planned: list[PlannedWrite], flagged_count: int) -> None:
    scored = sum(1 for item in planned if item.op.action == ACTION_SCORE)
    excluded = sum(1 for item in planned if item.op.action == ACTION_BLANK_EXCLUDED)
    stale = sum(1 for item in planned if item.op.action == ACTION_BLANK_STALE)
    log.info(
        "Plan: %s scored, %s excluded, %s flagged, %s stale (total writes %s)",
        scored,
        excluded,
        flagged_count,
        stale,
        len(planned),
    )


def _print_console_summary(planned: list[PlannedWrite], flagged_count: int, write_failures: int) -> None:
    scored = sum(1 for item in planned if item.op.action == ACTION_SCORE)
    excluded = sum(1 for item in planned if item.op.action == ACTION_BLANK_EXCLUDED)
    stale = sum(1 for item in planned if item.op.action == ACTION_BLANK_STALE)
    print(
        f"scored={scored} excluded={excluded} flagged={flagged_count} "
        f"stale={stale} write_failures={write_failures}"
    )


if __name__ == "__main__":
    sys.exit(main())
