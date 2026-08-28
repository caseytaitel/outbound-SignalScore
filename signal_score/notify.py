from __future__ import annotations

import logging
from pathlib import Path

from signal_score.constants import HUBSPOT_APP_URL

log = logging.getLogger(__name__)

APP_ID = "Realm.SignalScore"


def toast_spec(
    *,
    write_failures: int,
    flagged: int,
    aborted: bool,
    logs_dir: Path,
    hubspot_app_url: str = HUBSPOT_APP_URL,
) -> tuple[str, str, str]:
    """Return (body, launch, action_label) for the run's toast state."""
    logs_launch = logs_dir.resolve().as_uri()
    if aborted:
        return "Signal Score: Run aborted — see logs.", logs_launch, "Open logs"

    n = write_failures
    m = flagged
    if n == 0 and m == 0:
        return (
            "Signal Score: Complete — all target accounts updated.",
            hubspot_app_url,
            "Open HubSpot",
        )
    if n > 0 and m == 0:
        noun = "account" if n == 1 else "accounts"
        return f"Signal Score: {n} {noun} failed to write.", logs_launch, "Open logs"
    if n == 0 and m > 0:
        noun = "account" if m == 1 else "accounts"
        return f"Signal Score: {m} {noun} flagged for review.", logs_launch, "Open logs"
    return f"Signal Score: {n} failed, {m} flagged.", logs_launch, "Open logs"


def show_run_toast(
    *,
    write_failures: int,
    flagged: int,
    aborted: bool,
    logs_dir: Path,
    hubspot_app_url: str = HUBSPOT_APP_URL,
) -> None:
    body, launch, action_label = toast_spec(
        write_failures=write_failures,
        flagged=flagged,
        aborted=aborted,
        logs_dir=logs_dir,
        hubspot_app_url=hubspot_app_url,
    )
    _show(body, launch, action_label)


def _show(body: str, launch: str, action_label: str) -> None:
    try:
        from winotify import Notification
    except ImportError:
        log.warning("winotify is not installed; skipping Windows toast: %s", body)
        return
    try:
        toast = Notification(app_id=APP_ID, title="Signal Score", msg=body, duration="long")
        toast.add_actions(label=action_label, launch=launch)
        toast.show()
    except Exception:
        log.exception("Failed to show Windows toast")
