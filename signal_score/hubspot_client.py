from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import requests

from signal_score.constants import HUBSPOT_API_BASE

log = logging.getLogger(__name__)

SEARCH_PAGE_SIZE = 100
BATCH_UPDATE_SIZE = 100
SEARCH_RESULT_CAP = 10_000
MAX_429_ATTEMPTS = 8
REQUEST_TIMEOUT_SECONDS = 30


class HubSpotError(Exception):
    """Base HubSpot client error. Never include the auth token in messages."""


class HubSpotAuthError(HubSpotError):
    """401/403 — credentials rejected."""


class HubSpotRateLimitError(HubSpotError):
    """429 persisted after backoff attempts."""


class HubSpotPullError(HubSpotError):
    """Universe or stale search could not be completed."""


@dataclass
class Company:
    id: str
    properties: dict[str, Any] = field(default_factory=dict)

    @property
    def name(self) -> str:
        return str(self.properties.get("name") or "")


@dataclass
class BatchUpdateResult:
    succeeded_ids: list[str]
    failed_ids: dict[str, str]  # id -> error message


class HubSpotClient:
    def __init__(self, token: str, base_url: str = HUBSPOT_API_BASE) -> None:
        self._base_url = base_url.rstrip("/")
        self._session = requests.Session()
        self._session.headers.update(
            {
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "User-Agent": "Realm-SignalScore/1.0",
            }
        )

    def search_companies(
        self,
        filter_groups: list[dict[str, Any]],
        properties: list[str],
    ) -> list[Company]:
        results: list[Company] = []
        after: str | None = None
        while True:
            body: dict[str, Any] = {
                "filterGroups": filter_groups,
                "properties": properties,
                "limit": SEARCH_PAGE_SIZE,
            }
            if after:
                body["after"] = after
            data = self._request_json("POST", "/crm/v3/objects/companies/search", json=body)
            for raw in data.get("results") or []:
                results.append(
                    Company(
                        id=str(raw.get("id", "")),
                        properties=dict(raw.get("properties") or {}),
                    )
                )
            after = ((data.get("paging") or {}).get("next") or {}).get("after")
            if not after:
                break
            if len(results) >= SEARCH_RESULT_CAP:
                raise HubSpotPullError(
                    f"Company search hit the {SEARCH_RESULT_CAP:,} result cap; aborting incomplete pull"
                )
        return results

    def get_company(self, company_id: str, properties: list[str]) -> Company:
        data = self._request_json(
            "GET",
            f"/crm/v3/objects/companies/{company_id}",
            params={"properties": ",".join(properties)},
        )
        return Company(
            id=str(data.get("id") or company_id),
            properties=dict(data.get("properties") or {}),
        )

    def batch_update_signal_score(self, updates: list[tuple[str, str]]) -> BatchUpdateResult:
        """updates: list of (company_id, signal_score_value_as_string)."""
        payload = {
            "inputs": [
                {"id": company_id, "properties": {"signal_score": value}}
                for company_id, value in updates
            ]
        }
        data = self._request_json(
            "POST",
            "/crm/v3/objects/companies/batch/update",
            json=payload,
        )
        succeeded = [str(item.get("id", "")) for item in data.get("results") or [] if item.get("id")]
        failed: dict[str, str] = {}
        for error in data.get("errors") or []:
            message = str(error.get("message") or error.get("category") or "unknown HubSpot error")
            context = error.get("context") or {}
            ids = context.get("ids") or context.get("id") or []
            if isinstance(ids, str):
                ids = [ids]
            if not ids:
                for company_id, _value in updates:
                    if company_id not in succeeded:
                        failed[company_id] = message
                continue
            for company_id in ids:
                failed[str(company_id)] = message
        requested = {company_id for company_id, _value in updates}
        missing = requested - set(succeeded) - set(failed)
        for company_id in missing:
            failed[company_id] = "not present in HubSpot batch response"
        return BatchUpdateResult(succeeded_ids=succeeded, failed_ids=failed)

    def _request_json(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self._base_url}{path}"
        delay = 1.0
        last_status = None
        for attempt in range(1, MAX_429_ATTEMPTS + 1):
            try:
                response = self._session.request(
                    method,
                    url,
                    json=json,
                    params=params,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                )
            except requests.RequestException as exc:
                raise HubSpotError(f"{method} {path} network error") from exc

            last_status = response.status_code
            if response.status_code == 429:
                wait = _retry_after_seconds(response, delay)
                log.warning("HubSpot 429 on %s %s; backing off %.1fs (attempt %s)", method, path, wait, attempt)
                time.sleep(wait)
                delay = min(delay * 2, 30)
                continue
            if response.status_code in (401, 403):
                raise HubSpotAuthError(f"HubSpot auth failed ({response.status_code}) on {method} {path}")
            if response.status_code >= 400:
                raise HubSpotError(f"HubSpot {response.status_code} on {method} {path}: {_safe_error_body(response)}")
            if not response.content:
                return {}
            try:
                return response.json()
            except ValueError as exc:
                raise HubSpotError(f"HubSpot returned non-JSON on {method} {path}") from exc

        raise HubSpotRateLimitError(
            f"HubSpot 429 persisted after {MAX_429_ATTEMPTS} attempts on {method} {path} (last status {last_status})"
        )


def _retry_after_seconds(response: requests.Response, fallback: float) -> float:
    header = response.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), 30.0)
        except ValueError:
            pass
    return min(fallback, 30.0)


def _safe_error_body(response: requests.Response) -> str:
    text = (response.text or "").strip().replace("\n", " ")
    return text[:300] if text else "(empty body)"
