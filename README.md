# Signal Score Automation

Daily Python job that scores Realm’s HubSpot **target accounts** (`hs_is_target_account = true`) and writes a 0–100 value to the Company property `signal_score`. Universe, exclusions, and scores all come from the **Company object only** — no Deal or Owner data.

Full business rules live in [`Signal_Score_Automation_Spec.md`](Signal_Score_Automation_Spec.md).

---

## How to run

```bash
pip install -r requirements.txt
python run_signal_score.py --dry-run   # pull + score; writes CSV; no HubSpot writes, no toast
python run_signal_score.py             # live write to HubSpot + Windows toast
python -m pytest
```

Auth: `HUBSPOT_TOKEN` in `.env.local` (gitignored). Start the command from this repo root.

**Dry-run output:** `logs/dry_run_YYYYMMDD.csv` (UTC date) with columns `company_id`, `name`, `action`, `signal_score`, `reason`.

**Live-run output:** `logs/signal_score_YYYY-MM-DD.log` plus a Windows toast.

---

## After the toast

Every live run fires exactly one toast. Click through, then:

| Toast | What to do |
|---|---|
| **Complete** | Nothing. Click opens HubSpot. |
| **N failed to write** | Click opens `logs/`. Find `WRITE FAILED` lines, fix the cause, **re-run the full script**. Failed writes are not retried automatically. |
| **M flagged for review** | Click opens `logs/`. Search `FLAGGED`. Fix the underlying HubSpot data (see flag reasons below), then **re-run the full script** — there is no “update these M companies only” path. |
| **N failed, M flagged** | Do both of the above, then full re-run. |
| **Run aborted** | Pull/auth/network failed before any writes. Check the log, fix credentials/network, re-run. |

Dry-run does not toast.

---

## Scoring model

Final score = `MIN(100, sum of buckets)`. Tiers inside a bucket are mutually exclusive (highest qualifying wins). The +5 marketing bump is the only additive exception, and only when the marketing **base** tier is already > 0.

| Bucket | Max | Rule |
|---|---|---|
| SIEM detection | 0 | Gate only. Exclude if `siem_detected` is blank or `"Not Detected"`. |
| Website activity | 35 | UTC calendar days since `last_web_visit_cr`: 0–7 → 35; 8–28 → 18; 29–90 → 9; 91+ or blank → 0. Future date → flag, do not score. |
| Marketing events | 30 +5 | High-engagement → 30; else Channel type → 18; else General type → 10; else 0. Multi-value type: highest wins, not summed. Bump +5 if base > 0 and distinct events ≥ 2. Base 0 + distinct ≥ 2 → flag, do not score. |
| Intent | 20 | `competitor_intent` contains Cribl → 20; any other non-blank → 10; blank → 0. |
| Hiring | 15 | CISO or SOC-leader count > 0 → 15; else SOC team ≥ 10 → 10; 5–9 → 6; 1–4 → 3; else 0. |

Also **excluded** (score written blank, not zero): `hs_num_open_deals` > 0, or `intro_demo_complete_date` set. A scored account with no signals still gets `0`. Stale scores (property set but company is no longer a target account) are cleared to blank.

**Flag reasons:** `future_web_visit_date` · `marketing_events_without_type`

---

## Architecture

| Module | Responsibility |
|---|---|
| `run_signal_score.py` | Entry point for manual runs / Task Scheduler. |
| `signal_score/config.py` | Load `HUBSPOT_TOKEN` from `.env.local`; never log it. |
| `signal_score/hubspot_client.py` | Auth, Company search pagination, batch update, 429 backoff. |
| `signal_score/pull.py` | Target-account universe, Step 2 exclusion, stale-score query. |
| `signal_score/scoring.py` | Pure scoring + flag rules. No HubSpot I/O. |
| `signal_score/writeback.py` | Batch-write scores/blanks; continue on per-company failure. |
| `signal_score/notify.py` | End-of-run Windows toast (live runs only). |
| `signal_score/orchestrator.py` | Wires the above; `--dry-run`; log file. |
| `tests/` | Unit tests for scoring, exclusion, write failure handling, toasts. |

---

## Known limitations / not done yet

- **Open deals:** exclusion uses the Company property `hs_num_open_deals` only. Closed deals are not considered; the Deal object is never read.
- **Intro demo:** exclusion uses the Company property `intro_demo_complete_date` only. No Deal-object demo/stage check.
- **Flagged companies:** no efficient “re-score just the flagged set” workflow. After you fix those records in HubSpot, you must run the full refresh (every target account), not a patch of the flagged IDs.
- **Windows Task Scheduler is not set up yet.** Runs are manual (`python run_signal_score.py`).
- **Not pushed to GitHub yet.** This repo is local only.
