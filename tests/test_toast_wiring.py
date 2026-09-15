"""Pins the orchestrator -> notify seam.

test_notify.py covers `toast_spec` (the pure string logic) and orchestrator's own tests cover
the CLI, but nothing exercises `show_run_toast`, which is what orchestrator actually calls.
That call sits after `write_ops` on the live-run path only, so a kwarg mismatch would not show
up in the suite or in a --dry-run -- the first sign would be a traceback on a live run, after
the writes had already landed. `_show` swallows winotify failures, but a bad kwarg raises
before it is ever reached.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import signal_score.notify as notify
from signal_score.notify import show_run_toast


def _orchestrator_toast_call_kwargs() -> list[set[str]]:
    """Keyword names passed to show_run_toast at each call site in orchestrator.py."""
    source = Path(notify.__file__).with_name("orchestrator.py").read_text(encoding="utf-8")
    calls = [
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "show_run_toast"
    ]
    assert calls, "expected orchestrator.py to call show_run_toast"
    return [{kw.arg for kw in call.keywords if kw.arg} for call in calls]


def test_every_orchestrator_call_site_matches_the_toast_signature():
    """Drift-proof: fails if either side renames or adds a kwarg the other does not know."""
    signature = inspect.signature(show_run_toast)

    for kwargs in _orchestrator_toast_call_kwargs():
        signature.bind(**dict.fromkeys(kwargs))  # raises TypeError on a mismatch


def test_show_run_toast_builds_a_body_for_the_live_run_call(monkeypatch, tmp_path):
    captured: list[tuple[str, str, str]] = []
    monkeypatch.setattr(notify, "_show", lambda *args: captured.append(args))

    show_run_toast(
        write_failures=0,
        flagged=2,
        aborted=False,
        logs_dir=tmp_path,
        hubspot_app_url="https://app.hubspot.com/x",
        targets=986,
        candidates=2400,
    )

    assert len(captured) == 1
    body, _launch, _action_label = captured[0]
    assert body.strip()


def test_show_run_toast_builds_a_body_for_the_aborted_call(monkeypatch, tmp_path):
    """The abort path passes no targets/candidates and must still work on the defaults."""
    captured: list[tuple[str, str, str]] = []
    monkeypatch.setattr(notify, "_show", lambda *args: captured.append(args))

    show_run_toast(
        write_failures=0,
        flagged=0,
        aborted=True,
        logs_dir=tmp_path,
        hubspot_app_url="https://app.hubspot.com/x",
    )

    assert len(captured) == 1
    assert captured[0][0].strip()
