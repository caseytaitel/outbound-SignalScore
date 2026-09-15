from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from signal_score.hubspot_client import Company


@dataclass
class FakeHubSpotClient:
    """Duck-types HubSpotClient.search_companies for unit tests. No network calls.

    responses_by_call: results returned in call order, one list per search_companies call.
    """

    responses_by_call: list[list[Company]] = field(default_factory=list)
    calls: list[dict[str, Any]] = field(default_factory=list)

    def search_companies(self, filter_groups: list[dict[str, Any]], properties: list[str]) -> list[Company]:
        self.calls.append({"filter_groups": filter_groups, "properties": properties})
        index = len(self.calls) - 1
        if index < len(self.responses_by_call):
            return self.responses_by_call[index]
        return []
