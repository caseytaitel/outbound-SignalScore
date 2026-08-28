from __future__ import annotations

import argparse
import csv
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from signal_score.config import Config, ConfigError, load_config
from signal_score.constants import (
    ACTION_BLANK_EXCLUDED,
    ACTION_BLANK_FLAGGED,
    ACTION_BLANK_STALE,
    ACTION_SCORE,
    REASON_SCORED,
    REASON_SIEM,
    REASON_STALE,
    UNIVERSE_PROPERTIES,
)
from signal_score.hubspot_client import (
    Company,
    HubSpotAuthError,
    HubSpotClient,
    HubSpotError,
    HubSpotPullError,
    HubSpotRateLimitError,
)
from signal_score.notify import show_run_toast
from signal_score.pull import classify_exclusion, fetch_stale, fetch_universe
from signal_score.scoring import compute_score, is_true
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
    parser.add_argument(
        "--company-id",
        action="append",
        dest="company_ids",
        metavar="ID",
        help="Isolate to one HubSpot company id. Repeatable. Score/exclude/flag/write unless --write-blank.",
    )
    parser.add_argument(
        "--write-blank",
        action="store_true",
        help="Force-write signal_score blank for --company-id only (smoke test). Skips scoring.",
    )
    parser.add_argument(
        "--rescore-flagged",
        action="store_true",
        help="Re-score companies listed in today's logs/flagged_YYYYMMDD.txt (UTC).",
    )
    parser.add_argument(
        "--flagged-file",
        metavar="PATH",
        help="With --rescore-flagged, read this file instead of today's UTC flagged file.",
    )
    args = parser.parse_args(argv)

    if args.write_blank and not args.company_ids:
        parser.error("--write-blank requires --company-id")
    if args.flagged_file and not args.rescore_flagged:
        parser.error("--flagged-file requires --rescore-flagged")
    if args.rescore_flagged and args.company_ids:
        parser.error("--rescore-flagged cannot be combined with --company-id")
    if args.rescore_flagged and args.write_blank:
        parser.error("--rescore-flagged cannot be combined with --write-blank")

    try:
        config = load_config()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1

    setup_logging(config.logs_dir)

    if args.write_blank:
        return _run_blank_smoke_test(config, args.company_ids, dry_run=args.dry_run)

    if args.rescore_flagged:
        flagged_path = (
            Path(args.flagged_file) if args.flagged_file else default_flagged_path(config.logs_dir)
        )
        try:
            company_ids = read_flagged_ids(flagged_path)
        except FileNotFoundError:
            log.error("Flagged file not found: %s", flagged_path)
            print(
                f"Flagged file not found: {flagged_path}. "
                "Use --flagged-file PATH if the UTC date rolled.",
                file=sys.stderr,
            )
            return 1
        if not company_ids:
            log.info("No flagged companies in %s", flagged_path)
            print(f"No flagged companies in {flagged_path}.")
            return 0
        return _run_isolated(config, company_ids, dry_run=args.dry_run, source=str(flagged_path))

    if args.company_ids:
        return _run_isolated(config, args.company_ids, dry_run=args.dry_run, source="--company-id")

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
    flagged_path = write_flagged_file(config.logs_dir, planned)
    log.info("Flagged ID file: %s", flagged_path)

    if dry_run:
        csv_path = write_dry_run_csv(config.logs_dir, planned)
        log.info("Dry-run CSV written to %s", csv_path)
        print(f"Dry-run complete. Report: {csv_path}")
        print(f"Flagged IDs: {flagged_path}")
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


def plan_company(company: Company) -> PlannedWrite:
    """Classify one company: stale if not a target, else exclude / flag / score."""
    if not is_true(company.properties.get("hs_is_target_account")):
        return PlannedWrite(
            op=WriteOp(
                company_id=company.id,
                name=company.name,
                action=ACTION_BLANK_STALE,
                signal_score=blank_value(),
                reason=REASON_STALE,
            )
        )

    exclusion = classify_exclusion(company.properties)
    if exclusion.excluded:
        return PlannedWrite(
            op=WriteOp(
                company_id=company.id,
                name=company.name,
                action=ACTION_BLANK_EXCLUDED,
                signal_score=blank_value(),
                reason=exclusion.reason or REASON_SIEM,
            )
        )

    result = compute_score(company.properties)
    if result.flagged:
        reason = ";".join(result.flag_reasons)
        log.warning("FLAGGED (%s): %s %s", reason, company.id, company.name)
        return PlannedWrite(
            op=WriteOp(
                company_id=company.id,
                name=company.name,
                action=ACTION_BLANK_FLAGGED,
                signal_score=blank_value(),
                reason=reason,
            ),
            flagged=True,
        )

    assert result.score is not None
    return PlannedWrite(
        op=WriteOp(
            company_id=company.id,
            name=company.name,
            action=ACTION_SCORE,
            signal_score=score_value(result.score),
            reason=REASON_SCORED,
        )
    )


def plan_writes(client: HubSpotClient) -> list[PlannedWrite]:
    try:
        universe = fetch_universe(client)
        stale = fetch_stale(client)
    except (HubSpotAuthError, HubSpotPullError, HubSpotRateLimitError, HubSpotError) as exc:
        raise AbortRun(str(exc)) from exc

    log.info("Universe pull: %s target accounts", len(universe))
    planned = [plan_company(company) for company in universe]
    excluded = sum(1 for item in planned if item.op.action == ACTION_BLANK_EXCLUDED)
    log.info(
        "Excluded: %s; scoring candidates: %s",
        excluded,
        len(universe) - excluded,
    )

    universe_ids = {company.id for company in universe}
    stale_unique = [company for company in stale if company.id not in universe_ids]
    log.info("Stale scores to clear: %s", len(stale_unique))
    planned.extend(plan_company(company) for company in stale_unique)
    return planned


def _run_isolated(
    config: Config,
    company_ids: list[str],
    *,
    dry_run: bool,
    source: str,
) -> int:
    """GET + classify + optional write for specific ids. No toast, no flagged-file overwrite, no full CSV."""
    log.info("Isolated run starting (source=%s dry_run=%s ids=%s)", source, dry_run, len(company_ids))
    try:
        client = HubSpotClient(config.token, base_url=config.hubspot_base_url)
    except HubSpotError as exc:
        log.error("Isolated run aborted: %s", exc)
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1

    planned: list[PlannedWrite] = []
    fetch_failures = 0
    for company_id in company_ids:
        try:
            company = client.get_company(company_id, UNIVERSE_PROPERTIES)
        except (HubSpotAuthError, HubSpotError) as exc:
            log.error("GET failed for %s: %s", company_id, exc)
            print(f"GET failed for {company_id}: {exc}", file=sys.stderr)
            fetch_failures += 1
            continue
        item = plan_company(company)
        planned.append(item)
        _print_isolated_row(item)

    flagged_count = sum(1 for item in planned if item.flagged)
    _log_plan_summary(planned, flagged_count)

    if dry_run:
        print("Dry-run: no HubSpot write. Isolated dry-run does not overwrite dry_run_YYYYMMDD.csv.")
        _print_console_summary(planned, flagged_count, write_failures=0)
        return 0 if fetch_failures == 0 else 1

    failures = write_ops(client, [item.op for item in planned]) if planned else []
    log.info(
        "Isolated run complete: %s writes planned, %s failed, %s flagged, %s fetch failures",
        len(planned),
        len(failures),
        flagged_count,
        fetch_failures,
    )
    _print_console_summary(planned, flagged_count, write_failures=len(failures))
    return 0 if fetch_failures == 0 and not failures else 1


def _print_isolated_row(item: PlannedWrite) -> None:
    value = item.op.signal_score if item.op.signal_score != "" else "blank"
    print(
        f"{item.op.company_id} {item.op.name}: {item.op.action} "
        f"signal_score={value} reason={item.op.reason}"
    )


def _run_blank_smoke_test(config: Config, company_ids: list[str], *, dry_run: bool) -> int:
    """Force-write signal_score blank for one or more companies. No toast, no scoring."""
    log.info("Blank smoke test starting (ids=%s dry_run=%s)", company_ids, dry_run)
    try:
        client = HubSpotClient(config.token, base_url=config.hubspot_base_url)
    except HubSpotError as exc:
        log.error("Blank smoke test aborted: %s", exc)
        print(f"Aborted: {exc}", file=sys.stderr)
        return 1

    ops: list[WriteOp] = []
    fetch_failures = 0
    for company_id in company_ids:
        try:
            company = client.get_company(company_id, ["name", "signal_score"])
        except (HubSpotAuthError, HubSpotError) as exc:
            log.error("Blank smoke test aborted for %s: %s", company_id, exc)
            print(f"Aborted: {exc}", file=sys.stderr)
            fetch_failures += 1
            continue
        current = company.properties.get("signal_score") or ""
        log.info(
            "Current signal_score for %s %s is %r; will write blank",
            company.id,
            company.name,
            current,
        )
        print(f"{company.id} {company.name}: current signal_score={current!r}")
        ops.append(
            WriteOp(
                company_id=company.id,
                name=company.name,
                action=ACTION_BLANK_EXCLUDED,
                signal_score=blank_value(),
                reason="blank_smoke_test",
            )
        )

    if dry_run:
        print("Dry-run: would write signal_score='' (no HubSpot write).")
        return 0 if fetch_failures == 0 else 1

    failures = write_ops(client, ops) if ops else []
    if failures:
        for failure in failures:
            log.error("Blank smoke test write failed: %s %s", failure.company_id, failure.error)
            print(f"Write failed: {failure.company_id} {failure.error}", file=sys.stderr)
    elif ops:
        log.info("Blank smoke test wrote signal_score='' for %s companies", len(ops))
        print("Wrote signal_score blank. Confirm in HubSpot that the field is empty.")
    return 0 if fetch_failures == 0 and not failures else 1


def utc_date_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def default_flagged_path(logs_dir: Path) -> Path:
    return logs_dir / f"flagged_{utc_date_stamp()}.txt"


def write_flagged_file(logs_dir: Path, planned: list[PlannedWrite]) -> Path:
    path = default_flagged_path(logs_dir)
    flagged = [item for item in planned if item.flagged]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, delimiter="\t", lineterminator="\n")
        for item in flagged:
            writer.writerow([item.op.company_id, item.op.name, item.op.reason])
    return path


def read_flagged_ids(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    ids: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        ids.append(stripped.split()[0])
    return ids


def write_dry_run_csv(logs_dir: Path, planned: list[PlannedWrite]) -> Path:
    stamp = utc_date_stamp()
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
