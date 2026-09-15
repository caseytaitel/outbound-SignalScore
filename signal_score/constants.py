from __future__ import annotations

REASON_OPEN_DEALS = "open_deals"
REASON_INTRO_DEMO = "intro_demo"
REASON_SIEM = "siem_not_detected"
REASON_MARKETING_FLAG = "marketing_events_without_type"
REASON_FUTURE_VISIT = "future_web_visit_date"
REASON_STALE = "stale_not_target"
REASON_SCORED = "scored"

ACTION_SCORE = "score"
ACTION_BLANK_EXCLUDED = "blank_excluded"
ACTION_BLANK_FLAGGED = "blank_flagged"
ACTION_BLANK_STALE = "blank_stale"

SCOPE_TARGET = "target"
SCOPE_CANDIDATE = "candidate"

SIGNAL_SCORE_PROPERTY = "signal_score"
HUBSPOT_APP_URL = "https://app.hubspot.com/contacts/47829307/objects/0-2/views/71178118/list"
HUBSPOT_API_BASE = "https://api.hubapi.com"

# Abort before writing if a run plans more than this. Expected steady state is ~3,400
# (~986 target + ~2,400 candidate); this is the runaway-query circuit breaker.
MAX_PLANNED_WRITES = 6_000

UNIVERSE_PROPERTIES = [
    "name",
    "hs_is_target_account",
    "hs_num_open_deals",
    "intro_demo_complete_date",
    "siem_detected",
    "last_web_visit_cr",
    "high_engagement_event_attendee",
    "marketing_event_type",
    "distinct_marketing_events_attended",
    "competitor_intent",
    "common_room_hiring_for_ciso",
    "common_room_hiring_for_soc_leaders",
    "common_room_hiring_for_soc_team",
    "type",
    SIGNAL_SCORE_PROPERTY,
]
