# Signal Score Automation

Daily Python job that scores Realm’s HubSpot **target accounts** (`hs_is_target_account = true`) and writes a 0–100 value to the Company property `signal_score`. Universe, exclusions, and scores all come from the **Company object only** — no Deal or Owner data.

This README is the source of truth for business rules.

---

## How to run

From this repo root (not inside `signal_score/`):

```bash
pip install -r requirements.txt
python run_signal_score.py --dry-run   # full universe; CSV + flagged file; no HubSpot writes, no toast
python run_signal_score.py             # full live write to HubSpot + Windows toast
python -m pytest
```

Auth: `HUBSPOT_TOKEN` in `.env.local` (gitignored).

**Score / update specific companies*
* (same exclude / flag / score / write rules as the full job; no toast; does not overwrite the full dry-run CSV or the flagged-ID file):

```bash
python run_signal_score.py --company-id 123 --dry-run
python run_signal_score.py --company-id 123 --company-id 456
```

**Re-score flagged companies** after you fix their HubSpot data. Each full run writes `logs/flagged_YYYYMMDD.txt` (UTC date, TSV: `company_id`, `name`, `reason`; empty if none). Isolated runs do not overwrite that file.

```bash
python run_signal_score.py --rescore-flagged --dry-run
python run_signal_score.py --rescore-flagged
python run_signal_score.py --rescore-flagged --flagged-file logs/flagged_20260828.txt
```

`--rescore-flagged` defaults to today’s UTC file. If the date rolled overnight, pass `--flagged-file` using the path printed in the log.

**Force-blank smoke test** (skips scoring; use a company the job would blank anyway):

```bash
python run_signal_score.py --company-id 25005760952 --write-blank --dry-run
python run_signal_score.py --company-id 25005760952 --write-blank
```

**Full-run output:** `logs/dry_run_YYYYMMDD.csv` (dry-run only), `logs/flagged_YYYYMMDD.txt`, `logs/signal_score_YYYY-MM-DD.log`. Live runs also toast.  
CSV `action` is one of: `score` | `blank_excluded` | `blank_flagged` | `blank_stale`. CSV `scope` is `target`, `candidate`, or blank (stale) — see [Candidates](#candidates---include-candidates).

---

## Candidates (`--include-candidates`)

By default the universe is target accounts only. With `--include-candidates`, the job **also** scores
non-target companies that have real signal — candidates a human might promote onto the target list.

```bash
python run_signal_score.py --include-candidates --dry-run   # review first
python run_signal_score.py --include-candidates             # live
```

A candidate is a company where `hs_is_target_account` is not `true`, **and** Company `type` is not one of
`Customer` / `Reseller` / `MSSP` / `MSSP / Reseller` / `Alliance Partner` / `Vendor` / `Investor` / `Other`
(blank `type` **is** in scope — treated as an oversight, not a deliberate exclusion), **and** at least one
signal property is present. "Present" here is deliberately coarser than the scoring gates (`HAS_PROPERTY`,
`> 0`, `= true`): it only bounds the pull. The same `classify_exclusion` / `compute_score` used for target
accounts then decides the real outcome, so candidates and target accounts can never be scored by different
rules. A company that qualifies but scores 0 (e.g. a web visit older than 90 days) still gets `0`, same as
any other scored account.

**This job never writes `hs_is_target_account`.** The target list stays human-curated; candidates simply get
a `signal_score` so someone can review them and add the good ones deliberately.

Scope definitions change as follows when the flag is on:

- **Universe** (Step 1) = target accounts **+** candidates.
- **Stale** (Step 5) = populated `signal_score` and neither a target account nor a qualifying candidate. A
  company whose signal goes away falls out of the universe and gets blanked on the next run.

Guardrails:

- Candidates whose outcome is a blank **and** whose `signal_score` is already empty are not written at all —
  that would be a no-op. Target accounts keep the documented full-refresh behavior. Typical run: ~986 target +
  ~2,400 candidate writes.
- A run planning more than `MAX_PLANNED_WRITES` (`constants.py`, currently 6,000) aborts before writing
  anything, as a circuit breaker against a filter change or CRM data shift.
- The flag is **off by default**, so `run_signal_score.bat` / Task Scheduler behave exactly as before until
  someone turns it on. It is a rollout guardrail, not permanent architecture — once the expanded scope is
  trusted, either add the flag to the `.bat` or make it the default.

---

## After the toast

Every live run fires exactly one toast. Click through, then:

| Toast | What to do |
|---|---|
| **Complete — X targets, Y candidates updated** | Nothing. X/Y are in-scope writes (scored plus excluded/flagged blanks; stale and no-op candidate blanks omitted). Click opens the HubSpot company saved view ([view 71178118](https://app.hubspot.com/contacts/47829307/objects/0-2/views/71178118/list)). |
| **N failed to write** | Click opens `logs/`. Find `WRITE FAILED` lines, fix the cause, then **re-run the full script** or `python run_signal_score.py --company-id <id>` for those IDs. Failed writes are not retried automatically and are **not** listed in `flagged_*.txt`. |
| **M flagged for review** | Click opens `logs/`. Search `FLAGGED` or open `logs/flagged_YYYYMMDD.txt`. Fix the underlying HubSpot data, then `python run_signal_score.py --rescore-flagged --dry-run`, then `--rescore-flagged`. |
| **N failed, M flagged** | Re-score flagged with `--rescore-flagged`. Re-run failed writes with `--company-id` or a full live run. |
| **Run aborted** | Pull/auth/network failed before any writes. Check the log, fix credentials/network, re-run. |

Dry-run does not toast.

---

## Process flow

1. **Pull universe** — all companies where `hs_is_target_account` is `true`. Not list membership. With `--include-candidates`, also qualifying candidates.
2. **Exclude** if any of: open deals > 0, intro demo date set, SIEM blank or `"Not Detected"`. Scoring is never run on excluded accounts.
3. **Score or flag** survivors. Flag rules are not a fourth exclusion; they only apply after Step 2.
4. **Write** — scored → integer `signal_score`; excluded / flagged / stale → blank (`""`), never zero. A scored account with no signals still gets `0`.
5. **Clear stale** — `signal_score` populated AND out of scope: `hs_is_target_account` not true (false or blank/unset) and, with `--include-candidates`, not a qualifying candidate either.
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
| `type` | enum: `Prospect`, `Customer`, `Reseller`, `MSSP`, `MSSP / Reseller`, `Alliance Partner`, `Vendor`, `Investor`, `Other` | Candidate scope only (`--include-candidates`); blank is in scope |
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
| `signal_score/pull.py` | Universe (target accounts + candidates), candidate scope rules, Step 2 exclusion, stale-score query. |
| `signal_score/scoring.py` | Pure scoring + flag rules. No HubSpot I/O. |
| `signal_score/writeback.py` | Batch-write scores/blanks; continue on per-company failure. |
| `signal_score/notify.py` | End-of-run Windows toast (live runs only). |
| `signal_score/orchestrator.py` | Wires the above; `--dry-run`; `--company-id`; `--rescore-flagged`; log file. |
| `tests/` | Unit tests for candidate scope, `plan_company` classification, and the no-op-blank rule. (Does not yet cover the flagged-ID file — pre-existing gap.) |

`signal_score/` is the importable package. Do not run files inside it as scripts.

---

## Known limitations / not done yet

- **Open deals:** exclusion uses the Company property `hs_num_open_deals` only. Closed deals are not considered; the Deal object is never read.
- **Intro demo:** exclusion uses the Company property `intro_demo_complete_date` only. No Deal-object demo/stage check.
- **Isolated / `--rescore-flagged`:** updates only the IDs you pass (or the flagged file). It does not refresh the rest of the universe and does not run the global stale-clear (Step 5).
- **`--rescore-flagged` date:** uses today’s UTC `flagged_YYYYMMDD.txt`. A same-day full dry-run overwrites that file. If the UTC date rolled, pass `--flagged-file`.
- **Windows Task Scheduler is not set up yet.** Runs are manual (`python run_signal_score.py`).
