# Signal Score Automation

Daily Python job that scores Realm’s HubSpot **target accounts** (`hs_is_target_account = true`) and writes a 0–100 value to the Company property `signal_score`. Universe, exclusions, and scores all come from the **Company object only** — no Deal or Owner data.

This README is the source of truth for business rules.

---

## How to run

From this repo root (not inside `signal_score/`):

```bash
pip install -r requirements.txt
python run_signal_score.py --dry-run   # pull + score; writes CSV; no HubSpot writes, no toast
python run_signal_score.py             # live write to HubSpot + Windows toast
python -m pytest
```

Auth: `HUBSPOT_TOKEN` in `.env.local` (gitignored).

**Dry-run output:** `logs/dry_run_YYYYMMDD.csv` (UTC date) with columns `company_id`, `name`, `action`, `signal_score`, `reason`.  
`action` is one of: `score` | `blank_excluded` | `blank_flagged` | `blank_stale`.

**Live-run output:** `logs/signal_score_YYYY-MM-DD.log` plus a Windows toast.

---

## After the toast

Every live run fires exactly one toast. Click through, then:

| Toast | What to do |
|---|---|
| **Complete** | Nothing. Click opens HubSpot (`https://app.hubspot.com`). |
| **N failed to write** | Click opens `logs/`. Find `WRITE FAILED` lines, fix the cause, **re-run the full script**. Failed writes are not retried automatically. |
| **M flagged for review** | Click opens `logs/`. Search `FLAGGED`. Fix the underlying HubSpot data, then **re-run the full script** — there is no “update these M companies only” path. |
| **N failed, M flagged** | Do both of the above, then full re-run. |
| **Run aborted** | Pull/auth/network failed before any writes. Check the log, fix credentials/network, re-run. |

Dry-run does not toast.

---

## Process flow

1. **Pull universe** — all companies where `hs_is_target_account` is `true`. Not list membership.
2. **Exclude** if any of: open deals > 0, intro demo date set, SIEM blank or `"Not Detected"`. Scoring is never run on excluded accounts.
3. **Score or flag** survivors. Flag rules are not a fourth exclusion; they only apply after Step 2.
4. **Write** — scored → integer `signal_score`; excluded / flagged / stale → blank (`""`), never zero. A scored account with no signals still gets `0`.
5. **Clear stale** — `signal_score` populated AND `hs_is_target_account` is not true (false or blank/unset).
6. **Full refresh every run** — no incremental/delta. Every target account is rewritten even if the value is unchanged.

Step 2 exclusions take precedence over flags. An account that is excluded is counted as excluded only, even if it would also have matched a flag rule.

---

## Company properties used

All on the Company object.

| Internal name | Type | Used for |
|---|---|---|
| `name` | string | Logs / dry-run CSV only |
| `hs_is_target_account` | enum true/false | Universe filter; stale check |
| `hs_num_open_deals` | number | Exclusion |
| `intro_demo_complete_date` | date | Exclusion |
| `siem_detected` | enum: `"Manually Confirmed"`, `"Strong"`, `"Weak"`, `"Not Detected"` | Exclusion (gate, 0 points) |
| `last_web_visit_cr` | date | Website scoring; future-date flag |
| `high_engagement_event_attendee` | enum `"true"` / `"false"` | Marketing scoring |
| `marketing_event_type` | multi-value enum (semicolon-separated) | Marketing scoring |
| `distinct_marketing_events_attended` | number | Marketing bump / flag |
| `competitor_intent` | multi-value enum (semicolon-separated) | Intent scoring |
| `common_room_hiring_for_ciso` | number | Hiring scoring |
| `common_room_hiring_for_soc_leaders` | number | Hiring scoring |
| `common_room_hiring_for_soc_team` | number | Hiring scoring |
| `signal_score` | number 0–100 or blank | Output |

Blank/null number properties used in scoring or exclusion are treated as 0.

---

## Scoring model

Final score = `MIN(100, sum of buckets)`. Tiers inside a bucket are mutually exclusive (highest qualifying wins). The +5 marketing bump is the only additive exception, and only when the marketing **base** tier is already > 0.

All date math uses **UTC calendar days** (HubSpot date properties are midnight UTC). Reference date is the run’s UTC date. A visit today = 0 days ago.

| Bucket | Max | Rule |
|---|---|---|
| SIEM detection | 0 | Gate only. See exclusions. Does not add to the numeric score. |
| Website activity | 35 | UTC days since `last_web_visit_cr` (inclusive, no gaps): **0–7 → 35**; **8–28 → 18**; **29–90 → 9**; **91+ or blank → 0**. Exactly 7 days ago = 35, not 18. Future date (days ago < 0) → flag, do not score. |
| Marketing events | 30 +5 | `high_engagement_event_attendee` = true → **30**; else type contains `"Channel Event Attendee"` → **18**; else contains `"General Marketing Event Attendee"` → **10**; else **0**. Type is multi-value: if both Channel and General, Channel wins (do not sum). **Bump +5** if base > 0 and `distinct_marketing_events_attended` ≥ 2. Base 0 and distinct ≥ 2 → flag, do not apply the bump. |
| Intent | 20 | `competitor_intent` contains `"Cribl"` (case-insensitive substring) → **20**; any other non-blank → **10**; blank → **0**. |
| Hiring | 15 | `common_room_hiring_for_ciso` > 0 **or** `common_room_hiring_for_soc_leaders` > 0 → **15**; else SOC team ≥ 10 → **10**; 5–9 → **6**; 1–4 → **3**; else **0**. |

### Exclusions (write blank)

Any of:

- `hs_num_open_deals` > 0 (blank counts as 0 — not excluded)
- `intro_demo_complete_date` is set (any non-empty value)
- `siem_detected` is blank or `"Not Detected"` (case-insensitive)

Log reasons: `open_deals` · `intro_demo` · `siem_not_detected`

### Flags (write blank, count in M)

Run only on Step 2 survivors. Not scored. Logged so the two causes can be told apart. An account may match both; still counts as one flagged company.

| Reason string | When |
|---|---|
| `future_web_visit_date` | `last_web_visit_cr` is in the future (UTC days ago < 0) |
| `marketing_events_without_type` | Marketing base tier is 0 and `distinct_marketing_events_attended` ≥ 2 |

---

## Failure handling

- No automatic retries of failed writes. Log and continue to the next company.
- HTTP 429: backoff-and-continue (rate-limit compliance, not a write retry).
- Read/auth/network failure before writes: abort. Do not score or write a partial universe.
- A human reviews the log and re-runs the script.

---

## Architecture

| Module | Responsibility |
|---|---|
| `run_signal_score.py` | Entry point. Run this from the repo root. |
| `signal_score/config.py` | Load `HUBSPOT_TOKEN` from `.env.local`; never log it. |
| `signal_score/hubspot_client.py` | Auth, Company search pagination, batch update, 429 backoff. |
| `signal_score/pull.py` | Target-account universe, Step 2 exclusion, stale-score query. |
| `signal_score/scoring.py` | Pure scoring + flag rules. No HubSpot I/O. |
| `signal_score/writeback.py` | Batch-write scores/blanks; continue on per-company failure. |
| `signal_score/notify.py` | End-of-run Windows toast (live runs only). |
| `signal_score/orchestrator.py` | Wires the above; `--dry-run`; log file. |
| `tests/` | Unit tests for scoring, exclusion, write failure handling, toasts. |

`signal_score/` is the importable package. Do not run files inside it as scripts.

---

## Known limitations / not done yet

- **Open deals:** exclusion uses the Company property `hs_num_open_deals` only. Closed deals are not considered; the Deal object is never read.
- **Intro demo:** exclusion uses the Company property `intro_demo_complete_date` only. No Deal-object demo/stage check.
- **Flagged companies:** no efficient “re-score just the flagged set” workflow. After you fix those records in HubSpot, you must run the full refresh (every target account), not a patch of the flagged IDs.
- **Windows Task Scheduler is not set up yet.** Runs are manual (`python run_signal_score.py`).
- **Not pushed to GitHub yet.** This repo is local only.
