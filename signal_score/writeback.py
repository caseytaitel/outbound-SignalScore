from __future__ import annotations

import logging
from dataclasses import dataclass

from signal_score.constants import SIGNAL_SCORE_PROPERTY
from signal_score.hubspot_client import (
    BATCH_UPDATE_SIZE,
    HubSpotAuthError,
    HubSpotClient,
    HubSpotError,
    HubSpotRateLimitError,
)

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriteOp:
    company_id: str
    name: str
    action: str
    signal_score: str
    reason: str


@dataclass(frozen=True)
class WriteFailure:
    company_id: str
    name: str
    reason: str
    error: str


def write_ops(client: HubSpotClient, ops: list[WriteOp]) -> list[WriteFailure]:
    """Write all ops in batches of 100. Continue on per-item and per-batch failure. No retries except 429 inside the client."""
    failures: list[WriteFailure] = []
    for start in range(0, len(ops), BATCH_UPDATE_SIZE):
        batch = ops[start : start + BATCH_UPDATE_SIZE]
        updates = [(op.company_id, op.signal_score) for op in batch]
        by_id = {op.company_id: op for op in batch}
        try:
            result = client.batch_update_signal_score(updates)
        except (HubSpotAuthError, HubSpotRateLimitError, HubSpotError) as exc:
            message = str(exc)
            log.error("Batch write failed for %s companies: %s", len(batch), message)
            for op in batch:
                failures.append(
                    WriteFailure(
                        company_id=op.company_id,
                        name=op.name,
                        reason=op.reason,
                        error=message,
                    )
                )
            continue
        for company_id, error in result.failed_ids.items():
            op = by_id.get(company_id)
            failures.append(
                WriteFailure(
                    company_id=company_id,
                    name=op.name if op else "",
                    reason=op.reason if op else SIGNAL_SCORE_PROPERTY,
                    error=error,
                )
            )
            log.error(
                "WRITE FAILED: %s %s (%s) — %s",
                company_id,
                op.name if op else "",
                op.reason if op else "",
                error,
            )
    return failures


def score_value(score: int) -> str:
    return str(int(score))


def blank_value() -> str:
    return ""
